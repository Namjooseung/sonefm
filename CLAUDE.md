# SoneFM — 업무자동화 플랫폼 작업 폴더

공동체라디오 SoneFM 96.3의 프로그램들을 업무자동화 플랫폼으로 묶는 곳이다. 사용자와는 **한국어**로 대화한다.
각 앱은 자기 폴더에 따로 git 저장소와 CLAUDE.md를 가진다. 앱 코드를 고칠 때는 그 폴더의 CLAUDE.md 규칙을 따른다.
이 폴더 자체도 작은 git 저장소인데, **루트의 `*.md`와 `docs/`만** 담는다(앱 폴더는 `.gitignore` 허용 목록 밖).

## 구조

```
sonefm-platform (허브) ──명령줄──▶ sonefm-schedule (편성표)
        │                 └─────▶ radio2yt (유튜브 편집기)
        │                 └─────▶ (앞으로 추가될 앱)
        ├─ 업무일지: 국장 구글 시트 ← 드라이브 데스크톱 앱 폴더
        └─ 편성: 편성 직원이 '데이터 내보내기'한 .db ← 드라이브 데스크톱 앱 폴더
```

- **플랫폼 → 앱 단방향.** 앱끼리 서로 부르거나 서로의 데이터를 읽지 않는다. 여러 앱이 필요한 업무는 플랫폼이 레시피대로 차례로 부르고 앞 결과를 다음 앱에 넘긴다.
- 각 앱은 자기 저장소·자기 Python·자기 설치 파일. 한 프로세스로 합치지 않고, 앱의 화면 단독 사용은 그대로 둔다. 화면 코드를 합치거나 Python 버전을 맞추지 않는다.
- **개인 계정·개인 키·구글 API·서버 금지.** 구글은 드라이브 데스크톱 앱 로그인(방송국 계정)만. 개발자가 떠나도 방송국 PC에서 돌아가야 한다.
- 데이터 루트 `Documents\SoneFM\`: 편성 `data\`, 유튜브 영상 `유튜브\`, 플랫폼 `platform\`.

| 문서 | 내용 |
|---|---|
| [APP_CONTRACT.md](APP_CONTRACT.md) | 모든 앱이 지킬 규칙(계약 v1): 매니페스트, 호출, 결과 JSON, 종료 코드, 진행률, 비밀 |
| [PLATFORM_DESIGN.md](PLATFORM_DESIGN.md) | 플랫폼 설계: 사람·데이터 흐름, 업무일지·편성 받기, 레시피, 화면, DB |
| [INTEGRATION.md](INTEGRATION.md) | 첫 레시피 "유튜브 영상 만들기" 입출력 명세 |
| [docs/](docs/) | 비개발자 인수인계(설치·설정·문제 해결·레시피/앱 추가) |

## 앱

| 폴더 | 앱 | 명령(계약 v1) |
|---|---|---|
| `sonefm-platform/` | 업무자동화 허브(pywebview). 규칙은 [sonefm-platform/CLAUDE.md](sonefm-platform/CLAUDE.md) | — |
| `sonefm-schedule/` | 편성표 앱 | `lookup`, `month`, `set-prep`, `check-db` |
| `radio2yt/` | 유튜브 편집기 | `make-video` |

## 새 업무 유형 추가 (코드 수정 없음)

1. 필요한 앱 명령이 이미 있는지 각 앱의 `sonefm-app.json`에서 확인한다.
2. 레시피 JSON을 만든다(형식: PLATFORM_DESIGN.md 4절, 예: `sonefm-platform/recipes/youtube-video.json`). 기본으로 넣을 것은 `sonefm-platform/recipes/`, 현장에서 추가하는 것은 `Documents\SoneFM\platform\recipes\`.
3. `keywords`에 업무일지 글자에 나오는 낱말을 넣는다. 플랫폼 테스트 `test_기본_레시피_파일이_올바름`이 기본 레시피를 검사한다.

## 새 앱 추가

1. 앱 저장소를 이 폴더 아래에 만들고 자기 CLAUDE.md를 둔다.
2. APP_CONTRACT.md 10절 체크리스트: `sonefm-app.json`, `<cli> <명령> --input --result`, 결과 JSON·종료 코드, 명령줄 테스트, 설치 스크립트에 매니페스트 복사. 설치판 exe 주의(인코딩·처리 안 된 예외)는 4절.
3. 앱 폴더에 `PLATFORM.md`(바깥에서 보는 입출력)를 쓰고 아래에 `@<폴더>/PLATFORM.md`로 불러온다.
4. 그 앱을 쓰는 레시피를 추가한다. 플랫폼 코드는 고치지 않는다.

## 여러 세션이 동시에 작업할 때

저장소가 앱별로 나뉘어 있으니 한 세션은 한 앱 폴더만 맡는다. 이 폴더의 공통 문서(`*.md`)를 고치는 세션은 하나로 정한다.

### sonefm-schedule — SoneFM 편성표 앱

@sonefm-schedule/PLATFORM.md

### radio2yt — SoneFM 유튜브 편집기

@radio2yt/PLATFORM.md
