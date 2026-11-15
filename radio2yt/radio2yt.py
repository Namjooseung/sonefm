"""라디오 방송 mp3 → 유튜브용 mp4 (명령줄 버전).

사람이 쓸 때:
    python radio2yt.py input/                      # 폴더 안의 오디오 전부 (같은 이름의 이미지를 썸네일로)
    python radio2yt.py ep01.mp3 --thumb ep01.jpg   # 파일 하나
    python radio2yt.py ep01.mp3 --dry-run          # 분석만

플랫폼이 부를 때 (SoneFM 앱 계약 v1, 상위 폴더 APP_CONTRACT.md):
    python radio2yt.py make-video --input 입력.json --result 결과.json
    입력: audio, thumb, out_dir, title, description, dry_run, upload

진행 로그·진행률(@progress)은 표준에러, 결과 JSON 한 줄은 --result 파일과 표준출력 마지막 줄.
종료 코드: 0 전부 성공 / 2 일부 건너뜀 / 1 실패(하나도 못 만듦).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from engine import pipeline as P

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp", ".bmp"]
EXIT = {"ok": 0, "partial": 2, "error": 1}
# analyze의 단계별 진행률 구간 (파일 하나 = 0~1). 나머지는 결과 오디오 0.7~0.8, mp4 0.8~1
STAGES = {"decode": (0.0, 0.1), "analyze": (0.1, 0.6), "refine": (0.6, 0.7)}


def log(msg: str) -> None:
    if sys.stderr is not None:   # 콘솔 없는 exe에서는 표준에러가 없을 수 있다
        try:
            print(msg, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass   # 화면용 exe에서 처리 안 된 예외는 오류 창을 띄우고 멈춘다 → 로그 실패는 삼킨다


def fmt(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 3600):d}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def find_thumb(audio: Path) -> Path | None:
    for ext in IMAGE_EXTS:
        if audio.with_suffix(ext).exists():
            return audio.with_suffix(ext)
    images = [p for p in audio.parent.iterdir() if p.suffix.lower() in IMAGE_EXTS]
    return images[0] if len(images) == 1 else None


class Progress:
    """파일 i/n의 단계 진행률을 전체 0~100으로 바꿔 '@progress N 메시지'로 낸다."""

    def __init__(self, n: int):
        self.n, self.i, self.last = n, 0, -1

    def emit(self, f: float, msg: str) -> None:
        pct = int(100 * (self.i + min(max(f, 0.0), 1.0)) / self.n)
        if pct != self.last:
            self.last = pct
            log(f"@progress {pct} {msg}")

    def stage(self, lo: float, hi: float, msg: str):
        return lambda f: self.emit(lo + (hi - lo) * f, msg)


def process(audio: Path, thumb: Path | None, out_dir: Path, dry_run: bool, prog: Progress) -> tuple[Path | None, str | None]:
    """→ (만든 mp4 또는 None, 건너뛴 이유 또는 None). dry_run이면 (None, None)이 성공."""
    log(f"\n=== {audio.name} ===")
    thumb = thumb or find_thumb(audio)
    if thumb is None and not dry_run:
        log(f"  [건너뜀] 썸네일 없음 ({audio.stem}.jpg/.png 를 같은 폴더에 두세요)")
        return None, f"썸네일이 없습니다 ({audio.stem}.jpg 또는 .png를 같은 폴더에 두거나 썸네일을 지정하세요)"
    if thumb is not None and not thumb.is_file():
        return None, f"썸네일 파일을 찾지 못했습니다: {thumb}"

    log("  분석 중...")
    a = P.analyze(str(audio), progress=lambda stage, f: prog.stage(*STAGES[stage], "음악 구간 분석 중")(f))
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"원본: {audio.name}  ({fmt(a.total)})", f"썸네일: {thumb.name if thumb else '-'}",
             f"잘라낸 음악 구간 {len(a.cuts)}개:"]
    lines += [f"  {fmt(c.start)} ~ {fmt(c.end)}  ({c.length:5.1f}초)" for c in a.cuts]
    if a.short_music:
        lines.append(f"남긴 짧은 음악 {len(a.short_music)}개:")
        lines += [f"  {fmt(s.start)} ~ {fmt(s.end)}  ({s.length:5.1f}초)" for s in a.short_music]
    lines.append(f"최종 길이: {fmt(a.out_length)}  (잘라낸 시간 {fmt(a.cut_length)})")
    log("\n".join("  " + l for l in lines[2:]))
    detail = ["", "[원본 분석 구간]"] + [f"  {fmt(s)} ~ {fmt(e)}  {l}" for l, s, e in a.segments]
    (out_dir / f"{audio.stem}_cuts.txt").write_text("\n".join(lines + detail), encoding="utf-8")

    if dry_run:
        return None, None
    out = out_dir / f"{audio.stem}.mp4"
    part = out_dir / f"{audio.stem}.part.mp4"   # 중간에 끊겨도 최종 이름으로 반쪽 파일이 남지 않게
    with tempfile.TemporaryDirectory() as tmp:
        m4a = Path(tmp) / "result.m4a"
        log("  결과 오디오 만드는 중...")
        P.build_result_audio(a, str(m4a), progress=prog.stage(0.7, 0.8, "결과 오디오 만드는 중"))
        log(f"  mp4 만드는 중 → {out}")
        P.render_video(str(m4a), str(thumb), str(part), a.out_length, progress=prog.stage(0.8, 1.0, "영상 만드는 중"))
    part.replace(out)
    log("  완료")
    return out, None


def _upload(mp4: Path, title: str, description: str) -> tuple[str | None, str | None]:
    """명시적으로 요청했을 때만. → (영상 주소, 건너뛴 이유). 설정·로그인이 없으면 건너뛴다(앱은 죽지 않는다)."""
    try:
        from app import youtube
        youtube.client()
        if youtube.account() is None:
            return None, "유튜브 로그인 정보가 없어 업로드를 건너뜀 (화면 프로그램에서 방송국 계정으로 로그인 필요)"
        problem = youtube.check_text(title, description)
        if problem:
            return None, f"업로드 건너뜀: {problem}"
        return youtube.video_url(youtube.upload(str(mp4), title, description)), None
    except Exception as e:  # noqa: BLE001
        return None, f"업로드 건너뜀: {e}"


def run(args: dict[str, Any]) -> dict[str, Any]:
    """make-video. 결과는 계약 v1 형식의 dict."""
    def result(status, message, count=0, data=None, outputs=(), skipped=()):
        return {"status": status, "count": count, "data": data or {}, "outputs": [str(o) for o in outputs],
                "skipped": list(skipped), "message": message}

    src = Path(str(args.get("audio") or ""))
    if not args.get("audio") or not src.exists():
        return result("error", f"입력을 찾을 수 없습니다: {src}")
    if src.is_dir():
        jobs = [(f, None) for f in sorted(src.iterdir()) if f.suffix.lower() in AUDIO_EXTS]
    else:
        jobs = [(src, Path(args["thumb"]) if args.get("thumb") else None)]
    if not jobs:
        return result("error", f"{src} 에 오디오 파일이 없습니다.")

    out_dir = Path(args.get("out_dir") or "output")
    dry_run = bool(args.get("dry_run"))
    title, description = str(args.get("title") or ""), str(args.get("description") or "")
    prog = Progress(len(jobs))
    done, outputs, skipped, videos = 0, [], [], []
    for i, (audio, thumb) in enumerate(jobs):
        prog.i = i
        try:
            mp4, reason = process(audio, thumb, out_dir, dry_run, prog)
        except Exception as e:  # noqa: BLE001  한 파일이 실패해도 다음 파일은 처리한다
            log(traceback.format_exc())
            mp4, reason = None, f"처리 중 오류: {e}"
        if reason:
            skipped.append({"item": audio.name, "reason": reason})
            continue
        done += 1
        outputs.append(out_dir / f"{audio.stem}_cuts.txt")
        if mp4 is None:
            continue
        outputs.append(mp4)
        video = {"source": str(audio), "mp4": str(mp4), "url": None}
        if title:   # 제목·설명을 파일로도 남긴다 (사람이 유튜브에 직접 올릴 때 복사용)
            txt = out_dir / f"{audio.stem}_youtube.txt"
            txt.write_text(f"{title}\n\n{description}\n", encoding="utf-8")
            outputs.append(txt)
        if args.get("upload"):
            video["url"], reason = _upload(mp4, title or audio.stem, description)
            if reason:
                skipped.append({"item": f"{audio.name} 업로드", "reason": reason})
        videos.append(video)
    prog.i = len(jobs) - 1
    prog.emit(1.0, "끝")

    data = {"title": title, "description": description, "out_dir": str(out_dir.resolve()), "videos": videos,
            "mp4": videos[0]["mp4"] if videos else None}
    message = f"{len(jobs)}개 중 {done}개 처리" + (f", {len(skipped)}건 건너뜀" if skipped else "")
    status = "ok" if not skipped else ("partial" if done else "error")
    if status == "error" and len(skipped) == 1:
        message = skipped[0]["reason"]
    return result(status, message, done, data, outputs, skipped)


def emit(res: dict[str, Any], result_path: str | None) -> int:
    line = json.dumps(res, ensure_ascii=False)
    if result_path:
        Path(result_path).write_text(line, encoding="utf-8")
    if sys.stdout is not None:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
    log(res["message"])
    return EXIT[res["status"]]


def main(argv: list[str] | None = None) -> int:
    # 설치판 exe는 PYTHONIOENCODING을 무시하고 cp949로 연다 → 한글·기호가 깨지거나 오류 나지 않게 맞춘다
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["make-video"]:
        p = argparse.ArgumentParser(prog="radio2yt make-video")
        p.add_argument("--input", help="입력 JSON 파일")
        p.add_argument("--result", help="결과 JSON을 쓸 파일")
        ns = p.parse_args(argv[1:])
        try:
            args = json.loads(Path(ns.input).read_text(encoding="utf-8")) if ns.input else {}
            res = run(args)
        except Exception as e:  # noqa: BLE001
            log(traceback.format_exc())
            res = {"status": "error", "count": 0, "data": {}, "outputs": [], "skipped": [],
                   "message": f"처리 중 오류: {e}"}
        return emit(res, ns.result)

    p = argparse.ArgumentParser(description="라디오 mp3에서 긴 음악을 잘라내고 썸네일 mp4로 변환")
    p.add_argument("input", help="오디오 파일 또는 폴더")
    p.add_argument("--thumb", help="썸네일 이미지 (파일 하나 처리 시)")
    p.add_argument("-o", "--output", default="output", help="결과 폴더 (기본: output)")
    p.add_argument("--dry-run", action="store_true", help="분석만 하고 mp4는 만들지 않음")
    p.add_argument("--title", help="유튜브 제목 (<이름>_youtube.txt에 남김)")
    p.add_argument("--description", help="유튜브 설명")
    p.add_argument("--upload", action="store_true",
                   help="유튜브에 올리기 (방송국 공용 계정 로그인·Google 심사 후에만. 기본은 꺼짐)")
    p.add_argument("--result", help="결과 JSON을 쓸 파일")
    ns = p.parse_args(argv)
    return emit(run({"audio": ns.input, "thumb": ns.thumb, "out_dir": ns.output, "dry_run": ns.dry_run,
                     "title": ns.title, "description": ns.description, "upload": ns.upload}), ns.result)


if __name__ == "__main__":
    sys.exit(main())
