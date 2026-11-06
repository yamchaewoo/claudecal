# ClaudeCal 개발 폴더 되살리기 · 작업 흐름

이 저장소(`yamchaewoo/claudecal`)는 깃허브 페이지 사이트예요. 위젯 개발 폴더는 git이 아니라서, 위젯 코드는 `widget/`(배포한 최신본), 개발 도구는 `dev/`에 같이 올려 둬요.
위젯 사용법·구조는 `widget/README.md`를 보세요.

## 다른 컴퓨터에서 처음 시작

```
ClaudeCal/                ← 새 폴더 (아무 데나)
  site/                   ← git clone https://github.com/yamchaewoo/claudecal.git site
  claude_cal.pyw …        ← python site/dev/setup_dev.py 가 widget/ 에서 꺼내 놓음
  make_release.py …       ← 〃 dev/ 에서
  credentials.json        ← 깃허브에 없음! 원래 PC에서 비공개로 받기
```

1. 새 폴더 `ClaudeCal` 안에서 `git clone https://github.com/yamchaewoo/claudecal.git site`
2. `python site/dev/setup_dev.py`
3. `credentials.json`을 원래 PC에서 받아 `ClaudeCal/`에 넣기 (공개된 곳에 올리지 말 것)
4. `python -m pip install -r requirements.txt` → `claude_cal.pyw` 더블클릭

## 작업 흐름 (두 컴퓨터에서 번갈아 할 때)

- **시작할 때**: `site`에서 `git pull` → `python site/dev/setup_dev.py` (다른 컴퓨터에서 올린 위젯 코드로 맞춤. 내용이 다른 파일은 `_backup_…/`에 남김)
- **위젯을 고쳤으면**: `ClaudeCal`에서 `python make_release.py "바뀐 점 한 줄"` → `site`에서 커밋·푸시 (친구들 위젯은 6시간 안에 자동 업데이트)
- **폰 앱을 고쳤으면**: `site/app/` 고치고 커밋·푸시 (앱 다시 열면 반영). `calcore.py`의 지시문을 바꿨으면 `python make_app_prompt.py`도
- 위젯 코드는 `make_release.py`를 해야 `widget/`으로 넘어가요 — 안 올린 채 다른 컴퓨터로 가면 그쪽엔 없어요.
- 시험: `site`에서 `python -m http.server 8765` → `http://localhost:8765/app/?demo=1` (가짜 구글·가짜 Claude)

## 함정 메모

- `install.bat`은 **ASCII만** (한글 넣으면 친구 PC cmd에서 깨져 설치가 안 됨). 한글 안내는 `install.py`.
- `credentials.json`은 깃허브·공개된 곳에 올리지 않기 (배포 zip에만).
- 빠른 등록(⚡)은 PC `calcore.quick_parse`와 폰 `site/app/cal.js quickParse`가 같은 규칙 — **한쪽을 고치면 다른 쪽도**. 반복 색(`SERIES_COLORS`)·구글 색 표(`GOOGLE_COLORS`)·알림 규칙도 PC `stores.py`와 폰 `cal.js`/`app.js`가 같아야 해요.
- 구글 `calendarList.get`은 `primary`로 되지만 `patch`는 `primary`면 400(Invalid resource id) → get으로 받은 진짜 id로.
- 기본 알림 = 구글 캘린더 기본 알림 하나(권한 `calendar.calendarlist`). 종일 일정은 알림 없이(`overrides: []`) — 기본값을 따르면 구글이 30분을 복사해 전날 밤 11시 반에 울림.
- 끝 시간을 안 적으면 끝 = 시작(길이 0, `isPoint`). 같은 시각 끝을 '밤 넘김'으로 보면 안 됨(`end < start`일 때만 +1일).
- `.gitattributes`의 `widget/** -text`는 지우지 말 것 (줄바꿈이 바뀌면 sha가 달라져 자동 업데이트가 계속 실패).
- 깃허브 페이지 배포가 'waiting'에서 멈추면 빈 커밋을 한 번 더 푸시하면 새로 돌아감.
- 시험으로 위젯을 하위 프로세스로 띄울 때 `APPDATA`를 바꾸면 `PYTHONUSERBASE=%APPDATA%\Python`도 같이 넘길 것 (안 그러면 부품을 못 찾아 사용자 화면에 오류 창이 뜸).
