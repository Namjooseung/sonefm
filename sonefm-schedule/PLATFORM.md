# sonefm-schedule — 플랫폼 연동 안내

상위 폴더(`c:\DEV\Project\SoneFM`)의 통합 플랫폼에서 이 프로그램을 이해하고 데이터를 가져다 쓰기 위한 문서다.
개발 규칙은 [CLAUDE.md](CLAUDE.md), 요구사항·도메인 규칙·결정 기록은 [PLAN.md](PLAN.md), 시각 디자인은 [DESIGN.md](../DESIGN.md), 개발자 실행·빌드는 [README.md](README.md)가 기준이다. 이 문서는 **바깥에서 보는 데이터와 호출 방법**만 다룬다.

## 한 줄 요약

SoneFM 96.3 월간 **편성표 데스크톱 앱**(v1.1.0, Windows). 엑셀로 하던 편성표를 대신한다. 프로그램(주기·편성 자리)을 등록하면 한 달 편성을 만들고, 칸마다 본방/재방과 **회차를 자동 계산**한다. 결방·교체·특집, 검증, 월 상태(작성 중 → 확정 → 마감), 엑셀 가져오기/내보내기, 자동 백업이 있다.

```
프로그램 + 편성 자리(요일·시각·본방/재방·적용 기간) ──월 생성──> schedule_cells(날짜×시각 1칸)
                                                       └─ 회차는 저장하지 않고 읽을 때 계산
```

플랫폼에서 이 앱은 **"언제 무엇이 몇 화로 나가는가"의 기준 데이터**다. 이후 방송일지·월간 보고서·유튜브 제목/설명이 이 데이터를 쓰도록 설계했다(PLAN.md 6·10장).

## 실행 환경

| 항목 | 값 |
|---|---|
| OS | Windows 10/11, Microsoft Edge WebView2 런타임 필요 |
| Python | 3.12 (`sonefm-schedule\.venv`). 의존성: pywebview, openpyxl, holidays |
| 화면 | Vue 3 + Vite → HTML 한 파일(`frontend/dist/index.html`)을 pywebview가 `file://`로 로드. **로컬 서버·포트 없음** |
| DB | SQLite 파일 하나, 저널 모드 DELETE(WAL 금지) |
| 작업 폴더 | `sonefm-schedule` 폴더를 cwd로 두어야 `app` 패키지를 찾는다 |
| 저장소 | https://github.com/Namjooseung/sonefm 의 `sonefm-schedule/` (브랜치 `main`) |

## 실행

```
cd c:\DEV\Project\SoneFM\sonefm-schedule
.venv\Scripts\python app/main.py          # 앱 (frontend/dist 빌드 필요)
.venv\Scripts\python app/main.py --dev    # Vite 개발 서버(localhost:5173) 로드 + 개발자 도구
.venv\Scripts\python -m pytest -q         # 테스트
```

설치판: `SoneFM편성표_Setup.exe`(약 16MB, 관리자 권한 불필요) → `SoneFM.exe`. 화면 옵션은 `--dev` 하나다. 플랫폼용 명령줄은 아래 `--cli`.

## 명령줄 (플랫폼용, 앱 계약 v1)

상위 폴더 [APP_CONTRACT.md](../APP_CONTRACT.md) 규칙을 따른다. 명령과 입력·출력 목록은 [sonefm-app.json](sonefm-app.json)이 기준이다.

```
.venv\Scripts\python app/main.py --cli <명령> --input 입력.json --result 결과.json   # 개발
SoneFM.exe --cli <명령> --input 입력.json --result 결과.json                         # 설치판
```

| 명령 | 하는 일 |
|---|---|
| `lookup` | 날짜·시각 → 프로그램 이름, 회차(`get_month` 계산), 본방/재방, 제목용 `label`, 월 상태 |
| `month` | `get_month` 결과 전체 |
| `set-prep` | `prep_status` 변경 (서비스 경유: 마감 잠금·변경 기록) |
| `check-db` | 받은 편성 데이터 파일 검사 + 이 버전 형식으로 마이그레이션 (**사본에만**) |

- 모두 `db`(편성 데이터 파일 경로)를 받을 수 있고, 없으면 이 PC의 `config.json` DB를 쓴다. 플랫폼은 편성 직원이 내보낸 파일의 **사본**을 넘긴다.
- 사람이 칠 때는 `--date 2026-11-16 --hour 14 --db ...`처럼 인자로 줘도 된다. 코드: `app/cli.py`, 테스트: `tests/test_cli.py`.
- 화면 exe라 표준출력이 없을 수 있다 → 결과는 `--result` 파일이 기준.

시작할 때 순서: (DB가 없으면 `seed/schedule_seed.db` 복사) → 백업 → 마이그레이션 → 오래된 백업 정리.

## 데이터 위치

기본 루트 `%USERPROFILE%\Documents\SoneFM\` (환경 변수 `SONEFM_HOME`으로 바꿀 수 있다. 테스트는 반드시 임시 폴더로 바꿔서 실제 데이터를 건드리지 않는다)

| 경로 | 내용 |
|---|---|
| `config.json` | DB 위치, 백업 폴더, 백업 보관 개수(기본 30). **DB 위치는 사용자가 바꿀 수 있으니 이 파일에서 읽는다** |
| `data\schedule.db` | 편성 DB (기본 위치) |
| `backups\` | 실행 시·가져오기 전 자동 백업 |
| `logs\app.log` | 로그 |

radio2yt의 기본 영상 저장 위치(`Documents\SoneFM\유튜브\`)와 같은 `Documents\SoneFM` 아래를 쓴다.

## 데이터 모델 요약

모든 테이블 행은 UUID `id`, `created_at`, `updated_at`을 가진다. 스키마 버전은 `PRAGMA user_version`(현재 마이그레이션 0001~0006). 자세한 컬럼은 PLAN.md 4장과 `app/db/migrations/`.

| 테이블 | 내용 |
|---|---|
| `programs` | 이름, 분야(교양/오락), 청취자참여, 제작 주체, 주기(`weekly/biweekly/multi_weekly/monthly_nth/irregular/oneoff`), 시작 회차, 시작·종료일, 진행자, 특집 여부, `show_episode` |
| `program_aliases` | 엑셀 이름 매칭용 별칭 |
| `program_slots` | 편성 자리: 요일(0=월), 시각(6~22), 본방/재방, `valid_from~valid_to`(개편 이력) |
| `schedule_cells` | 실제 칸, `UNIQUE(date, hour)`. `kind`(본방/재방), `source`(rule/manual/import), `exception_type`, `rerun_episode_override`, `prep_status`(미입고/입고/송출완료), `actual_change_note` |
| `cancellations` | 결방(회차 계산에서 뺄 원래 칸) |
| `month_plans` | 월 상태: 작성중/확정/마감 |
| `holidays`, `settings`, `change_log` | 임시공휴일, 운영 기준값(`fixed_program_ids`, `offweek_policy_default`), 변경 기록 |

**꼭 알아야 할 규칙**

- **회차는 DB에 없다.** 본방 회차 = 시작 회차 + 그 전까지 실제로 나간 본방 수, 재방 회차 = 직전 본방 회차(수동 지정이 있으면 그 값). raw SQL로 칸을 읽으면 회차를 얻을 수 없으니 아래 Python 함수를 쓴다.
- 방송은 매일 06~22시 시작, 17칸, 모든 프로그램 57분. 06시 `굿모닝!`, 22시 `굿바이!`는 고정 편성(`fixed_program_ids`, 회차 숨김).
- **주의 소속은 그 주 월요일이 속한 달**(10/26~11/1 주는 10월 화면). 하지만 **집계·보고서는 달력 날짜 기준**(11월 보고서에는 11/1 포함).
- 편성표는 "계획"이다. 당일 사고는 `actual_change_note`(실제 변경)로 남는다. 방송일지·보고서는 실제 기준으로 만들어야 한다.
- `prep_status`(송출 파일 준비 상태)는 지금 수동 토글이고, 유튜브 파이프라인이 쓰도록 만들어 둔 칸이다.

## 데이터 읽기 (Python, cwd = sonefm-schedule)

```python
from app.config import load_config
from app.db.connection import open_db
from app.services import schedule as S

cfg = load_config()                      # config.json 읽기 (없으면 기본값으로 만들어 저장함)
with open_db(cfg.db_path) as conn:       # with가 끝나면 커밋하고 닫는다
    m = S.get_month(conn, 2026, 11)      # 화면이 쓰는 것과 같은 결과
m["cells"]["2026-11-03|14"]
# {"program_id", "name", "category", "kind": "본방"|"재방", "episode": 23|None,
#  "rerun_mark", "waiting"(본방 대기), "is_fixed", "is_special", "show_episode",
#  "manual", "imported", "exception_type", "prep_status", "actual_change_note"}
m["weeks"], m["status"], m["issues"], m["totals"]   # 주 목록, 월 상태, 검증 결과, 총 방송분
```

- `get_month`는 **화면 기준(월요일 소속 주)** 이라 앞뒤 달 날짜와 직전 달 참고 주까지 담는다. 달력 날짜로 집계할 때는 키의 날짜로 걸러 쓴다.
- 날짜 기준 집계 함수(`domain/aggregate.py`)는 PLAN.md에 계획만 있고 **아직 없다**. 방송일지·보고서를 붙일 때 이 앱의 `app/domain/`에 순수 함수로 만들고 테스트를 붙인다.
- 그 밖의 공개 함수: `app/services/programs.py`(`list_programs`, `get_program`), `app/services/excel.py`(`export_months(conn, [(2026, 11)], path)` 엑셀 내보내기). 결과는 모두 `{"ok": bool, ..., "errors": [...]}` 형식이다.
- 화면이 부르는 함수 전체 목록은 `app/api.py`의 `Api` 클래스다(`window.pywebview.api.*`).

## 플랫폼에서 연동할 때 지킬 것

- 이 앱의 코드를 고칠 때는 `sonefm-schedule\CLAUDE.md` 규칙을 따른다. 특히:
  - SQL은 `app/repository/`에서만, 비즈니스 규칙은 `app/domain/` 순수 함수 + pytest.
  - 스키마 변경은 **새 마이그레이션 파일로만**. 다른 프로그램이 이 DB에 자기 테이블을 붙이고 싶어도 이 규칙을 따른다.
  - 서버·클라우드·개인 계정/API 키에 의존하는 기능 금지(개발자가 떠나도 방송국 PC 한 대에서 돌아가야 함). radio2yt의 유튜브 업로드(Google OAuth)는 이 원칙과 충돌하므로, 편성표 앱 안으로 합칠 때는 사용자와 먼저 정한다.
  - 운영 기준값은 settings 테이블에. 하드코딩 금지.
- **DB에 직접 쓰지 않는다.** 칸을 바꾸면 회차·변경 기록·마감 잠금 처리가 필요하므로 `app/services/`를 거친다. 읽기만 할 때도 앱이 켜져 있으면 잠금이 겹칠 수 있으니 연결을 짧게 쓴다.
- 마감(`마감`)된 달은 보고서를 낸 달이다. 수정하지 않는다.
- PLAN.md의 "이후 범위"(유튜브·동기화·홍보물, 방송일지·보고서는 구현됨)는 **사용자가 요청하기 전에 구현하지 않는다**. 원래 계획은 이 앱 왼쪽 메뉴에 탭으로 붙이는 것이다(PLAN.md 1·10장).
- 비개발자 문서: 기능을 추가하면 `docs/인수인계_사용법.md`, 오류 대처는 `docs/인수인계_문제해결.md`에 쌓는다.

## 플랫폼과 맞닿는 곳

플랫폼(`sonefm-platform`)이 이 앱을 명령줄로 부른다. 앱은 플랫폼이나 다른 앱을 모른다.

| 편성표 쪽 | 플랫폼에서 쓰는 곳 |
|---|---|
| `lookup` | 유튜브 영상 레시피: 제목·설명 재료 (상위 [INTEGRATION.md](../INTEGRATION.md)) |
| `check-db` | 편성 직원이 드라이브에 내보낸 데이터 파일을 받을 때 검사 |
| 설정 화면 "드라이브 폴더 (보내기·가져오기)" (config.json `auto_export_dir`, 켜짐 여부 `auto_export_enabled`) | 편성을 바꾸고 60초 뒤·앱을 닫을 때·폴더를 고를 때 드라이브 폴더에 `SoneFM편성표_데이터.db`(고정 이름, 임시 파일 후 교체)로 사본을 올림 → 업무자동화 PC로 편성을 넘기는 수단. 수동 "데이터 내보내기"도 그대로 있음. 같은 폴더에서 **"드라이브에서 가져오기"**(가장 최근 .db를 확인 후 백업·교체, 편성표만 쓰는 다른 PC용) |

처리 상태(유튜브 완료 등)는 플랫폼 DB에 기록한다. 받은 사본에 쓰면 다음 파일이 올 때 덮이기 때문이다.

## 현재 상태 (2026-11-15)

- P0~P8 구현 완료, v1.0.0 설치 파일까지 만듦. 테스트 `pytest -q`.
- 법정 편성비율 기능 제거(2026-11-13 사용자 결정), 플랫폼용 명령줄 `--cli` 추가(v1.1.0).
- 방송실시결과 보고서 추가(2026-11-15): 왼쪽 메뉴 보고서 탭, `app/services/report.py` `export_report(conn, year, month, path)` → hwpx(한글 있으면 hwp도). 제작 주체는 자체/외부 둘(마이그레이션 0005), 대중음악 목록 settings `music_program_ids`(0006).
- 방송일지 만들기 추가(2026-11-15): 왼쪽 메뉴 방송일지 탭, `app/services/journal.py` `export_journal(conn, year, month, path)`. 편성표(계획) 그대로, 달력 날짜 기준 하루 1시트. 명령줄 명령은 아직 없음.
- 남은 확인: 개발 도구 없는 다른 PC에서 설치 → 실행 → 한 달 편성 생성(사용자 확인 필요), 방송국 PC 윈도우 버전, 프로그램별 재방 자리·시작 회차 목록(PLAN.md 9장).
