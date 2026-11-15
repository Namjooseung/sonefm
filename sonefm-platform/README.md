# SoneFM 업무자동화 (sonefm-platform)

SoneFM 96.3 업무자동화 PC용 허브 앱. 국장 업무일지에서 오늘 업무를 가져와 보여주고, 업무마다 담당 앱(편성표, 유튜브 편집기 …)을 명령줄로 불러 처리한다.

- 사용·설치·문제 해결(비개발자): [../docs/](../docs/)
- 설계: [../PLATFORM_DESIGN.md](../PLATFORM_DESIGN.md) · 앱 규칙: [../APP_CONTRACT.md](../APP_CONTRACT.md) · 첫 레시피: [../INTEGRATION.md](../INTEGRATION.md)
- 개발 규칙: [CLAUDE.md](CLAUDE.md)

## 개발

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m hub
.venv\Scripts\python -m pytest -q
```

개발 중에는 설정 → "앱 폴더"에 `..\sonefm-schedule`, `..\radio2yt` 경로를 넣는다(각 폴더에 `.venv`가 있어야 한다). 설치된 앱은 자동으로 찾는다.

## 빌드

```
.venv\Scripts\python -m PyInstaller packaging/platform.spec --noconfirm
ISCC packaging\installer.iss
```

결과: `packaging\Output\SoneFM-Platform-Setup-<버전>.exe` (관리자 권한 없이 설치, `%LOCALAPPDATA%\Programs\SoneFM 업무자동화\`).
