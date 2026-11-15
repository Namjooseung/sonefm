# 첫 레시피: 유튜브 영상 만들기

국장 업무일지의 "유튜브 …" 줄 하나를 처리한다. 플랫폼이 편성표 앱에서 프로그램 이름·회차를 받아 유튜브 편집기에 넘긴다. 앱끼리는 서로 모른다.
레시피 파일: `sonefm-platform/recipes/youtube-video.json`. 규칙: [APP_CONTRACT.md](APP_CONTRACT.md), 설계: [PLATFORM_DESIGN.md](PLATFORM_DESIGN.md).

```
업무 "유튜브 화 14시 마을살이"
  → 폼: 날짜(업무 날짜) · 시각(글자의 '14시') · 송출 mp3 · 썸네일(지난번 값)
  → ① sonefm-schedule lookup   (받은 편성 사본)        → 이름·회차·label
  → ② radio2yt make-video      (제목 = label)         → mp4, 제목·설명 파일
  → 플랫폼 DB: runs 기록, 업무 '완료'
  → 결과: [mp4 폴더 열기] [제목 복사] [설명 복사] [유튜브 스튜디오] — 업로드는 사람이
```

## ① 편성 조회

플랫폼이 쓰는 입력 파일과 실행:

```json
{"date": "2026-11-17", "hour": 11, "db": "C:/Users/<사용자>/Documents/SoneFM/platform/schedule/schedule.db"}
```
```
SoneFM.exe --cli lookup --input in.json --result out.json          (설치판)
.venv\Scripts\python.exe app/main.py --cli lookup ...                (개발)
```

결과(종료 코드 0):

```json
{"status": "ok", "count": 1,
 "data": {"date": "2026-11-17", "hour": 11, "name": "마을살이 정류장", "episode": 43, "kind": "본방",
          "label": "마을살이 정류장 43회", "category": "교양", "program_id": "…",
          "prep_status": null, "month_status": "확정"},
 "outputs": [], "skipped": [], "message": "2026-11-17 11시: 마을살이 정류장 43회 (본방)"}
```

- 회차는 편성표 앱의 `get_month` 계산 그대로다. 회차를 숨기는 프로그램(고정 편성 등)은 `episode: null`, `label`은 이름만.
- 빈칸이면 `status: error`, 종료 코드 1, `message: "2026-11-17 15시는 편성표에 빈칸입니다."` → 플랫폼은 여기서 멈춘다.
- 그 달이 '확정'·'마감'이 아니면 `message` 끝에 "아직 '작성중' 상태입니다"가 붙는다. 플랫폼은 결과 화면에 그대로 보여준다.

## ② 영상 만들기

```json
{"audio": "D:/송출/20261117_11.mp3",
 "thumb": "C:/Users/<사용자>/Documents/SoneFM/썸네일/마을살이.png",
 "title": "마을살이 정류장 43회",
 "description": "마을살이 정류장 43회 (2026-11-17 방송)\nSoneFM 96.3 수원공동체라디오",
 "out_dir": "C:/Users/<사용자>/Documents/SoneFM/유튜브"}
```
```
SoneFMEditor.exe --cli make-video --input in.json --result out.json   (설치판)
.venv\Scripts\python.exe radio2yt.py make-video ...                    (개발)
```

진행률(표준에러): `@progress 12 음악 구간 분석 중` … `@progress 100 끝`. 시간 제한 1800초.

결과(종료 코드 0):

```json
{"status": "ok", "count": 1,
 "data": {"title": "마을살이 정류장 43회", "description": "…", "out_dir": "C:/…/SoneFM/유튜브",
          "mp4": "C:/…/SoneFM/유튜브/20261117_11.mp4",
          "videos": [{"source": "D:/송출/20261117_11.mp3", "mp4": "C:/…/20261117_11.mp4", "url": null}]},
 "outputs": ["C:/…/20261117_11_cuts.txt", "C:/…/20261117_11.mp4", "C:/…/20261117_11_youtube.txt"],
 "skipped": [], "message": "1개 중 1개 처리"}
```

실패 예(종료 코드 1): `{"status": "error", "count": 0, "skipped": [{"item": "20261117_11.mp3", "reason": "썸네일 파일을 찾지 못했습니다: …"}], "message": "썸네일 파일을 찾지 못했습니다: …", …}`

## 결과 처리 (플랫폼)

| 결과 | 플랫폼이 하는 일 |
|---|---|
| 두 단계 모두 ok | `runs`에 성공 기록, 업무 key를 '완료'로(`task_marks`), 결과 버튼 표시 |
| ②가 partial | 기록은 '일부', 업무는 완료로 하지 않음, 건너뛴 이유 표시 |
| 어느 단계든 error | 그 단계에서 멈춤, '실패' 기록, "n단계(앱 이름)에서 실패: 이유" 표시. 같은 업무를 다시 실행할 수 있음 |
| 시간 초과 | 프로세스 트리 종료, '실패(시간 초과)' |

**prep_status(편성표의 송출 준비 상태)는 바꾸지 않는다.** 플랫폼이 가진 것은 편성 직원 PC에서 받은 사본이라, 사본에 써도 다음 파일이 오면 덮인다. 처리 상태는 플랫폼 DB(`runs`, `task_marks`)가 기준이다. 편성은 항상 편성 직원 PC에서 짜므로(2026-11-15 결정) 업무자동화 쪽에서 편성 데이터를 고치는 단계는 두지 않는다.

## 유튜브 업로드

- 기본은 **사람이 올린다**: [mp4 폴더 열기] → [유튜브 스튜디오] → [제목 복사]/[설명 복사].
- radio2yt의 자동 업로드(`upload: true`)는 **방송국 공용 Google 계정으로 로그인하고, Google 앱 심사(audit)를 통과한 뒤에만** 레시피 `args`에 `"upload": true`를 넣어 켠다. 그 전에는 YouTube가 영상을 비공개로 잠그고, 개인 계정에 묶이면 사람이 떠날 때 끊긴다. 로그인 정보가 없으면 업로드만 건너뛰고(종료 코드 2) 영상은 정상이다.
