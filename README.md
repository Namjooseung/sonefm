# SoneFM 업무자동화

공동체라디오 **SoneFM 96.3** 실무용 프로그램 묶음입니다. 엑셀로 짜던 편성표, 손으로 자르던 유튜브 영상 편집, 업무일지에 적힌 반복 업무를 PC 한 대에서 처리하도록 만들었습니다. 서버나 클라우드 API 없이 방송국 PC에 설치해서 씁니다.

```
              ┌──────── 국장 업무일지 (구글 시트 → 드라이브 데스크톱 앱 폴더)
              ▼
     sonefm-platform  ── 업무 분류(레시피) ──▶  앱을 명령줄로 차례로 호출
       (업무자동화 허브)                          │
              ▲                                  ├─▶ sonefm-schedule  편성표: 날짜·시각 → 프로그램·회차
              │                                  └─▶ radio2yt         송출 mp3 → 음악을 잘라낸 유튜브 mp4
              └──────── 편성 담당이 내보낸 편성 데이터 (드라이브 폴더)
```

| 폴더 | 무엇 | 기술 |
|---|---|---|
| [`sonefm-platform/`](sonefm-platform/) | 업무자동화 허브. 오늘 업무를 받아 레시피대로 앱을 실행하고 결과·기록을 남김 | Python 3.12, pywebview, 빌드 없는 HTML/JS |
| [`sonefm-schedule/`](sonefm-schedule/) | 월간 편성표 앱. 편성 생성·편집, 회차 자동 계산, 엑셀 연동, 방송일지·방송실시결과 문서 | Python 3.12, pywebview, Vue 3 + Vite, SQLite |
| [`radio2yt/`](radio2yt/) | 유튜브 편집기. 말/음악 구분(ONNX)으로 20초 이상 음악만 잘라 썸네일 정지 화면 mp4 생성 | Python 3.13, PySide6, onnxruntime, ffmpeg |

## 설계에서 신경 쓴 점

- **앱은 독립, 허브는 명령줄로만 연결.** 앱마다 자기 Python·설치 파일·화면을 그대로 두고, 허브는 [앱 계약](APP_CONTRACT.md)(매니페스트 `sonefm-app.json`, JSON 결과, 종료 코드, 진행률 형식)만 안다. 새 앱은 계약만 지키면 붙는다.
- **새 업무 유형은 코드 수정 없이 레시피(JSON) 추가.** 단계마다 앱 명령을 적고, 앞 단계 결과를 `{info.data.label}`처럼 다음 단계 입력에 넘긴다.
- **개발자가 떠나도 돌아가게.** 구글 API·서비스 계정·서버 없이 드라이브 데스크톱 앱 폴더와 공유 링크만 쓴다. 업무일지가 끊기면 조용히 옛 데이터로 돌지 않고 화면에 경고한다.
- **회차는 저장하지 않고 계산**(편성표), **멘트는 BGM이 깔려도 자르지 않음**(유튜브 편집기, 원본 TensorFlow 모델과 비트 단위로 같은 ONNX 변환).

## 문서

- 설계: [PLATFORM_DESIGN.md](PLATFORM_DESIGN.md) · 앱 계약: [APP_CONTRACT.md](APP_CONTRACT.md) · 첫 레시피 명세: [INTEGRATION.md](INTEGRATION.md)
- 비개발자 인수인계: [docs/](docs/) (설치와 사용, 문제 해결, 업무 유형·앱 추가)
- 각 앱의 개발 규칙: 폴더마다 `CLAUDE.md`, 바깥에서 보는 입출력: `PLATFORM.md`

## 실행 (개발)

```
cd sonefm-platform
실행.bat          # exe 없이 체험 데이터로 바로 실행 (옆 두 앱의 .venv 필요)
```

각 앱의 설치·빌드는 폴더 안 README를 참고하세요. Windows 10/11 전용입니다.
