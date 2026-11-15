"""명령줄(radio2yt.py) 결과 JSON·종료 코드 점검 (SoneFM 앱 계약 v1).

    .venv\\Scripts\\python -m unittest tests.test_cli
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["APPDATA"] = tempfile.mkdtemp()  # 실제 사용자 데이터를 건드리지 않는다
import radio2yt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
Span = lambda s, e: SimpleNamespace(start=s, end=e, length=e - s)  # noqa: E731


def fake_analyze(path, progress=None, is_cancelled=lambda: False):
    for stage in ("decode", "analyze", "refine"):
        progress and progress(stage, 1.0)
    return SimpleNamespace(total=100.0, cuts=[Span(10, 40)], short_music=[], out_length=70.0, cut_length=30.0,
                           segments=[("speech", 0, 10), ("music", 10, 40), ("speech", 40, 100)])


def fake_render(m4a, thumb, out, duration, progress=None, is_cancelled=lambda: False):
    Path(out).write_bytes(b"mp4")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.out = self.dir / "out"
        patches = [mock.patch.object(radio2yt.P, "analyze", fake_analyze),
                   mock.patch.object(radio2yt.P, "build_result_audio", lambda *a, **k: None),
                   mock.patch.object(radio2yt.P, "render_video", fake_render)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def audio(self, name, thumb=True):
        a = self.dir / name
        a.write_bytes(b"x")
        if thumb:
            a.with_suffix(".png").write_bytes(b"png")
        return a

    def run_cli(self, args):
        inp, res = self.dir / "in.json", self.dir / "res.json"
        inp.write_text(json.dumps(args), encoding="utf-8")
        with mock.patch("sys.stdout", new=io.StringIO()), \
                mock.patch("sys.stderr", new=io.StringIO()):
            code = radio2yt.main(["make-video", "--input", str(inp), "--result", str(res)])
        return code, json.loads(res.read_text(encoding="utf-8"))

    def test_성공_제목_설명_파일과_데이터(self):
        a = self.audio("ep56.mp3")
        code, r = self.run_cli({"audio": str(a), "out_dir": str(self.out), "title": "마을살이 42회",
                                "description": "설명"})
        self.assertEqual((code, r["status"], r["count"]), (0, "ok", 1))
        mp4 = self.out / "ep56.mp4"
        self.assertTrue(mp4.exists())
        self.assertFalse((self.out / "ep56.part.mp4").exists())
        self.assertEqual(r["data"]["mp4"], str(mp4))
        self.assertIn(str(mp4), r["outputs"])
        self.assertEqual((self.out / "ep56_youtube.txt").read_text(encoding="utf-8"), "마을살이 42회\n\n설명\n")
        self.assertEqual(set(r), {"status", "count", "data", "outputs", "skipped", "message"})
        self.assertIsNone(r["data"]["videos"][0]["url"])   # 업로드는 기본 꺼짐

    def test_썸네일_없으면_실패_종료코드_1(self):
        a = self.audio("ep57.mp3", thumb=False)
        code, r = self.run_cli({"audio": str(a), "out_dir": str(self.out)})
        self.assertEqual((code, r["status"], r["count"]), (1, "error", 0))
        self.assertIn("썸네일", r["message"])
        self.assertEqual(r["skipped"][0]["item"], "ep57.mp3")

    def test_폴더_일부_건너뛰면_종료코드_2(self):
        self.audio("a.mp3")
        self.audio("b.mp3", thumb=False)
        (self.dir / "c.jpg").write_bytes(b"jpg")   # 이미지가 둘이라 b는 썸네일을 못 찾는다
        code, r = self.run_cli({"audio": str(self.dir), "out_dir": str(self.out)})
        self.assertEqual((code, r["status"], r["count"]), (2, "partial", 1))
        self.assertEqual([s["item"] for s in r["skipped"]], ["b.mp3"])

    def test_한_파일_오류여도_다음_파일은_처리(self):
        self.audio("a.mp3")
        self.audio("b.mp3")
        calls = []

        def flaky(path, progress=None, is_cancelled=lambda: False):
            calls.append(path)
            if len(calls) == 1:
                raise RuntimeError("디코딩 실패")
            return fake_analyze(path, progress)
        with mock.patch.object(radio2yt.P, "analyze", flaky):
            code, r = self.run_cli({"audio": str(self.dir), "out_dir": str(self.out), "thumb": None})
        self.assertEqual(code, 2)
        self.assertIn("디코딩 실패", r["skipped"][0]["reason"])

    def test_입력_없음(self):
        code, r = self.run_cli({"audio": str(self.dir / "없음.mp3")})
        self.assertEqual((code, r["status"]), (1, "error"))

    def test_업로드_요청해도_로그인_없으면_건너뛰고_죽지_않음(self):
        a = self.audio("ep58.mp3")
        with mock.patch("app.youtube.client", return_value={}), mock.patch("app.youtube.account", return_value=None):
            code, r = self.run_cli({"audio": str(a), "out_dir": str(self.out), "upload": True})
        self.assertEqual((code, r["status"], r["count"]), (2, "partial", 1))
        self.assertIn("로그인", r["skipped"][0]["reason"])
        self.assertTrue((self.out / "ep58.mp4").exists())

    def test_진행률_형식(self):
        a = self.audio("ep59.mp3")
        err = self.dir / "err.txt"
        with open(err, "w", encoding="utf-8") as f, mock.patch("sys.stderr", new=f), \
                mock.patch("sys.stdout", new=io.StringIO()):
            radio2yt.main(["make-video", "--input", str(self._write({"audio": str(a), "out_dir": str(self.out)}))])
        pcts = [int(l.split()[1]) for l in err.read_text(encoding="utf-8").splitlines() if l.startswith("@progress ")]
        self.assertTrue(pcts and pcts == sorted(pcts) and pcts[-1] == 100)

    def test_사람용_명령줄도_결과_JSON(self):
        a = self.audio("ep60.mp3")
        res = self.dir / "res.json"
        with mock.patch("sys.stdout", new=io.StringIO()), \
                mock.patch("sys.stderr", new=io.StringIO()):
            code = radio2yt.main([str(a), "-o", str(self.out), "--dry-run", "--result", str(res)])
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "ep60_cuts.txt").exists())
        self.assertIsNone(json.loads(res.read_text(encoding="utf-8"))["data"]["mp4"])

    def test_매니페스트(self):
        m = json.loads((ROOT / "sonefm-app.json").read_text(encoding="utf-8"))
        from app import __version__
        self.assertEqual((m["contract"], m["id"], m["version"]), (1, "radio2yt", __version__))
        self.assertEqual(set(m["commands"]), {"make-video"})

    def _write(self, args):
        p = self.dir / "in2.json"
        p.write_text(json.dumps(args), encoding="utf-8")
        return p


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 없음")
class RealEngineTest(unittest.TestCase):
    """진짜 엔진으로 끝까지: 합성 오디오(말 대신 잡음 + 25초 음 + 잡음) → mp4. 하위 프로세스로 표준출력 마지막 줄 확인."""

    def test_끝까지(self):
        d = Path(tempfile.mkdtemp())
        audio = d / "syn.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
                        str(audio)], check=True)
        shutil.copy(ROOT / "assets" / "thumbnails" / "기본.png", d / "syn.png")
        inp = d / "in.json"
        inp.write_text(json.dumps({"audio": str(audio), "out_dir": str(d / "out"), "title": "합성"}), encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, "radio2yt.py", "make-video", "--input", str(inp)], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual((p.returncode, r["status"]), (0, "ok"), p.stderr[-2000:])
        self.assertTrue(Path(r["data"]["mp4"]).stat().st_size > 0)
        self.assertIn("@progress 100", p.stderr)


if __name__ == "__main__":
    unittest.main()
