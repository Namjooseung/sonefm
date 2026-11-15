# CLAUDE.md — SoneFM 업무자동화 플랫폼 (sonefm-platform)

업무자동화 PC 한 대에서 도는 허브 앱. 국장 업무일지(구글 시트)에서 오늘 업무를 가져와, 업무마다 레시피대로 다른 앱(편성표, 유튜브 편집기 …)을 명령줄로 부른다. 사용자와는 **한국어**로 대화하고 보고는 짧게.

설계: 상위 [../PLATFORM_DESIGN.md](../PLATFORM_DESIGN.md), 앱 규칙: [../APP_CONTRACT.md](../APP_CONTRACT.md), 첫 레시피: [../INTEGRATION.md](../INTEGRATION.md).

## 절대 규칙

- **앱별 코드를 플랫폼에 넣지 않는다.** 앱은 매니페스트(`sonefm-app.json`)와 레시피(`recipes/*.json`)로만 다룬다. `hub/registry.py`, `hub/runner.py`, `hub/recipes.py`에 특정 앱 id가 나오면 잘못이다. (예외: `hub/schedule_inbox.py`는 편성 파일 받기라서 `sonefm-schedule`의 `check-db` 명령을 부른다.)
- **앱의 DB·파일을 직접 읽지 않는다.** 필요한 값은 앱 명령의 결과(`data`)로 받는다.
- **구글 API·서비스 계정·OAuth·개인 키 금지.** 업무일지는 드라이브 데스크톱 앱 폴더의 `.gsheet` → 공유 링크 xlsx 내려받기, 편성은 드라이브 폴더의 `.db` 파일. 서버 없음.
- 처리 상태는 플랫폼 DB(`Documents\SoneFM\platform\platform.db`)에. 받은 편성 사본에 쓰지 않는다.
- 사용자에게 보이는 문구는 한국어 존댓말, 오류는 "무엇을 하면 되는지"까지.
- `Api`(hub/api.py)에 **공개 속성을 두지 않는다.** pywebview가 js_api의 공개 속성을 모두 훑다가 창 객체 같은 것에서 죽는다(실제로 겪음). 내부 값은 `_`로 시작.

## 구조

| 파일 | 하는 일 |
|---|---|
| `hub/paths.py` | `Documents\SoneFM\platform\` (테스트는 `SONEFM_HOME`), 설정 `config.json` |
| `hub/registry.py` | 매니페스트 읽기, 앱 찾기(`app_dirs` → `%LOCALAPPDATA%\Programs\*`, `%ProgramFiles%\*`), 계약 버전 확인 |
| `hub/runner.py` | 공통 실행기: subprocess, `--input/--result`, `@progress`, 시간 초과(프로세스 트리 종료), 결과 파싱 |
| `hub/recipes.py` | 레시피 불러오기, `{...}` 값 끼워 넣기, 키워드 분류(`Classifier` 자리), 여러 단계 실행 |
| `hub/worklog.py` | 업무일지 받기·읽기(세 가지 시트 모양), 이상 경고 |
| `hub/schedule_inbox.py` | 편성 폴더의 최신 `.db` → 검사(`check-db`) → 사본 |
| `hub/db.py` | 실행 기록·업무 표시·직접 입력 업무·키-값 |
| `hub/api.py`, `hub/main.py` | 화면 연결, pywebview 창 |
| `ui/` | 화면(빌드 없음: index.html, app.js, style.css). 색·글꼴은 루트 ../DESIGN.md(모든 앱 공통) 토큰 |
| `recipes/` | 기본 레시피. 사용자 레시피는 `Documents\SoneFM\platform\recipes\`(같은 id면 우선) |

## 명령어

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m hub                 # 실행 (실제 데이터)
실행.bat                                    # 체험: demo-data\ + tools/demo_setup.py가 견본 준비
.venv\Scripts\python -m pytest -q           # 테스트 (옆 폴더에 두 앱 개발 환경 + ffmpeg가 있으면 실제 앱 연동 테스트도 돈다)
.venv\Scripts\python -m PyInstaller packaging/platform.spec --noconfirm   # → dist\SoneFMPlatform\
ISCC packaging\installer.iss               # Inno Setup 6 → packaging\Output\ (UTF-8 BOM 유지)
```

## 테스트 원칙

- 가짜 앱(`tests/fake_app.py`, `tests/conftest.py`의 `make_app`)으로 실행기·레시피를 검증한다. 성공·부분성공·실패·시간 초과·깨진 결과.
- 업무일지는 내려받기 함수를 주입해서 가짜 xlsx로. 실제 구글 접속 없음.
- `conftest.py`가 `SONEFM_HOME`과 `LOCALAPPDATA`/`ProgramFiles`를 임시 폴더로 바꾼다. 실제 데이터와 설치된 앱을 건드리지 않는다.
- 화면을 고치면 실제로 띄워서 확인한다(`logs\platform.log`의 "화면 열림: {bridge: true …}").

## 남은 일

- 실제 업무일지 시트 확인(PLATFORM_DESIGN.md 2절 "실제 시트에서 확인할 것" 4가지) → 쓰지 않는 시트 모양 처리는 지운다.
- 방송국 PC(개발 도구 없음)에서 설치 확인. 개발 PC에서는 2026-11-15에 세 설치 파일로 설치 → 자동 탐색 → 설치판 exe로 유튜브 레시피 끝까지 → 제거까지 확인함. 설치 파일은 상위 `../배포/`.

## 인수인계 문서

비개발자 문서는 상위 `../docs/`에 쌓는다(설치·설정·오류 대처·레시피/앱 추가).
