# radio2yt — 플랫폼 연동 안내

상위 폴더(`c:\DEV\Project\SoneFM`)의 통합 플랫폼에서 이 프로그램을 이해하고 호출하기 위한 문서다.
개발 규칙·주의점은 [CLAUDE.md](CLAUDE.md), 요구사항과 결정 기록은 [REQUIREMENTS.md](REQUIREMENTS.md)가 기준이다. 이 문서는 **바깥에서 보는 입출력과 호출 방법**만 다룬다.

## 한 줄 요약

SoneFM 96.3 내부 편집용 Windows 프로그램(v1.0). 라디오 송출 mp3(57분 내외)에서 **20초 이상 이어지는 음악만 잘라내고**, 썸네일 한 장을 정지 화면으로 붙여 **유튜브용 mp4(1920×1080, h264/aac 192k)** 를 만든다. 멘트는 BGM이 깔려 있어도 자르지 않는다. 결과 화면에서 유튜브 업로드까지 할 수 있다.

```
mp3 ──분석(말/음악 구분, ONNX)──> 자를 구간 결정 ──> 결과 오디오(m4a) ──+ 썸네일──> mp4 ──> (유튜브 업로드)
```

## 실행 환경

| 항목 | 값 |
|---|---|
| OS | Windows 전용 (로그인 정보 암호화에 DPAPI 사용) |
| Python | `radio2yt\.venv\Scripts\python.exe` (3.13, numpy·onnxruntime·PySide6) |
| ffmpeg | PATH에 있어야 함. 설치판은 `_internal\bin\ffmpeg.exe`를 씀. ffprobe는 안 씀 |
| 작업 폴더 | **`radio2yt` 폴더를 현재 폴더(cwd)로 두고 실행**해야 `engine`, `app` 패키지를 찾는다 |
| 성능 | 57분 방송 분석 약 27초, 최대 메모리 약 0.87GB |
| 저장소 | https://github.com/Namjooseung/radio2yt (공개, MIT, 브랜치 `main`) |

## 호출 방법

### 1. 명령줄 (플랫폼에서 부르기에 가장 쉬움)

```
cd c:\DEV\Project\SoneFM\radio2yt
.venv\Scripts\python radio2yt.py <파일.mp3 | 폴더> [--thumb 이미지] [-o 결과폴더] [--dry-run]
```

- **입력:** 오디오 파일 하나, 또는 폴더(안의 `.mp3 .wav .m4a .aac .flac .ogg` 전부를 처리).
- **썸네일:** `--thumb`(파일 하나일 때만). 없으면 같은 이름의 이미지(`ep01.jpg` 등)를 쓰고, 폴더에 이미지가 하나뿐이면 그것을 쓴다. 썸네일을 못 찾으면 그 파일은 건너뛴다(`--dry-run`이면 썸네일 없이 진행).
- **결과 (`-o`, 기본 `output\`):**
  - `<이름>.mp4`: 결과 영상(`--dry-run`이면 만들지 않음)
  - `<이름>_cuts.txt`: 잘라낸 구간, 남긴 짧은 음악, 최종 길이, 원본 분석 구간 전체
- **주의:**
  - 썸네일이 없어 **건너뛴 파일이 있어도 종료 코드는 0**이다. 0이 아닌 경우는 입력 경로가 없거나 폴더에 오디오가 없을 때, 그리고 분석·ffmpeg 오류로 멈췄을 때(뒤 파일은 처리 안 됨)뿐이다. 성공 여부는 마지막 줄 `N/M개 처리 완료.`와 mp4가 생겼는지로 판단한다.
  - 한글을 출력한다. 다른 프로세스에서 출력을 읽을 때는 `PYTHONIOENCODING=utf-8`을 넣어야 글자가 깨지지 않는다.
  - 유튜브 업로드는 명령줄에 없다.

### 2. 엔진 점검 (`--selftest`)

```
.venv\Scripts\python sonefm.py --selftest 파일.mp3      # 개발 환경
SoneFMEditor.exe --selftest 파일.mp3                    # 설치판
```

화면 없이 분석 → 결과 오디오 → mp4 → 재생 모듈까지 돌린다. 종료 코드는 성공 0, 실패 1이다. 자세한 결과는 `%APPDATA%\SoneFM 유튜브 편집기\selftest.txt`에 남고, 마지막 줄이 `RESULT: OK` 또는 `RESULT: FAIL ...`이다.

### 3. Python에서 직접 (cwd = radio2yt)

```python
from engine import pipeline as P
a = P.analyze("ep.mp3", progress=None, is_cancelled=lambda: False)   # -> Analysis
a.cuts, a.short_music, a.keeps   # Span 목록 (.start .end .length, 초 단위)
a.total, a.out_length, a.cut_length
P.build_result_audio(a, "result.m4a")                     # 남는 구간을 이어 붙인 AAC
P.render_video("result.m4a", "thumb.png", "out.mp4", a.out_length)
P.probe_info("ep.mp3")   # {"duration": 초, "kbps": ..., "mb": ...}
```

- 오래 걸리는 함수는 모두 `progress`(0~1 콜백)와 `is_cancelled`를 받는다. 취소되면 `pipeline.Cancelled`를 던진다.
- 기준값(최소 음악 20초, 여유 1초, 페이드 0.3초)은 `engine/pipeline.py` 상단 상수로 **고정**이다. 사용자 결정이라 바깥에서 바꾸는 옵션을 만들지 않는다.

### 4. 유튜브 업로드 (Python)

```python
from app import youtube
youtube.account()                         # 로그인된 채널 이름, 없으면 None
youtube.check_text(title, description)    # 문제 있으면 이유 문자열, 괜찮으면 None
vid = youtube.upload("out.mp4", title, description, progress=None, is_cancelled=lambda: False)
youtube.video_url(vid)                    # https://youtu.be/<id>
```

- **로그인은 사람이 해야 한다.** `youtube.login()`이 브라우저를 열고 기다린다. 한 번 로그인하면 `%APPDATA%\SoneFM 유튜브 편집기\youtube_login.bin`에 남아서, 그 뒤로는 화면 없이도 `upload()`가 동작한다. 단, **같은 Windows 사용자 계정**에서만 된다(DPAPI 암호화).
- `assets\client_secret.json`(Google Cloud 데스크톱 앱 OAuth 클라이언트)이 있어야 한다. 이 파일은 저장소에 없다(gitignore).
- 공개 범위는 `public`으로 고정했지만, Google 앱 심사(audit)를 받기 전에는 YouTube가 **비공개로 잠근다**. 하루 업로드 할당량에도 제한이 있다.
- 2026-11-15 기준: 코드와 가짜 서버 테스트까지 끝났고, **실제 Google 계정으로는 아직 확인하지 않았다**.

### 5. 화면 프로그램

```
.venv\Scripts\python -m app [파일.mp3]      # 개발 환경 (실행.bat도 같음)
SoneFMEditor.exe [파일.mp3]                 # 설치판
```

파일을 인자로 주면 열자마자 썸네일 선택 창이 뜬다. 흐름은 열기 → 썸네일 선택 → 분석 → 결과 확인(재생) → 영상 만들기 → 유튜브에 올리기 순이다.

## 데이터 위치

`%APPDATA%\SoneFM 유튜브 편집기\` (테스트할 때는 `APPDATA` 환경 변수를 임시 폴더로 바꿔서 실제 데이터를 건드리지 않는다)

| 파일 | 내용 |
|---|---|
| `settings.json` | 마지막 폴더·썸네일·저장 위치, `uploads`(최근 업로드 20개의 제목·설명·URL·시각) |
| `history.json` | 화면에서 만든 영상 기록(최근 200개): `source, output, thumbnail, orig_len, out_len, time` |
| `thumbnails\` | 사용자가 추가한 썸네일 |
| `youtube_login.bin` | 유튜브 로그인 정보(DPAPI 암호화) |
| `selftest.txt`, `log.txt` | 점검 결과, 설치판 로그 |

화면에서 만든 영상의 기본 저장 위치는 `%USERPROFILE%\Documents\SoneFM\유튜브\`다.

## 배포

설치 파일 `SoneFM-YoutubeEditor-Setup-<버전>.exe`(약 111MB, ffmpeg·모델 포함, 관리자 권한 필요 없음)는 GitHub Releases에 있다. 만드는 방법은 CLAUDE.md의 "배포" 절을 따른다.

## 플랫폼에서 연동할 때 지킬 것

- 이 프로그램의 코드를 고칠 때는 `radio2yt\CLAUDE.md`의 규칙을 따른다(디자인은 `design/DESIGN.md` 우선, 엔진 결과는 원본 TensorFlow와 비트 단위로 같아야 함, Qt 스레드 규칙 등).
- 바깥에서는 **명령줄이나 공개 함수(위 3·4절)로 부르는 것**을 우선한다. 내부 화면 코드(`app/views`, `app/widgets`)에 기대지 않는다.
- 사용자 선호는 "기본기 우선, 설정 화면 없음"이다. 플랫폼 쪽에서 기준값 조정 같은 옵션을 붙이기 전에 먼저 사용자에게 확인한다.

## 남은 일 (2026-11-15)

- 유튜브 업로드를 실제 계정으로 확인하기(`client_secret.json` 필요)
- 56화 21:57~22:21 구간(24초 음악)을 잘라도 되는지 사용자 확인 대기
- 실제 기본 썸네일로 교체(지금은 임시 1장)
- 여러 회차 한 번에 처리: 화면에는 없고 명령줄로만 가능
