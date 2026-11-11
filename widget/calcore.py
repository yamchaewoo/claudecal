# -*- coding: utf-8 -*-
"""ClaudeCal 핵심 로직 (화면 코드 없음)
  · 한 줄 해석: 단순한 등록은 빠른 해석기(quick_parse)가 바로, 나머지는 Claude(API 또는 Claude Code)가
  · 해석 결과(actions) → 할 일 목록(plan) → 저장소(stores.py)에 적용, 되돌리기 기록을 돌려준다
"""
import os
import re
import sys
import json
import shutil
import datetime
import subprocess

IS_WIN = sys.platform.startswith("win")
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
RR_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ClaudeCal")
CONF_FILE = os.path.join(APP_DIR, "config.json")

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}

DEFAULT_CONF = {
    "hotkey": "ctrl+shift+space",
    "engine": "auto",          # auto(API 키가 있으면 API, 없으면 Claude Code) | api | cli
    "model": "sonnet",         # haiku | sonnet | opus  (API·Claude Code 공통)
    "api_key": "",             # Anthropic API 키 (콘솔에서 발급)
    "quick_parse": True,       # 단순한 등록은 Claude 없이 바로
    "confirm_delete": True,    # 말로 지울 때 한 번 묻기
    "notify": True,            # 일정 전에 윈도우 알림
    "remind_min": 10,          # 기본 알림 (분 전)
    "on_top": True,
    "autostart": True,
    "timezone": "Asia/Seoul",
    "days_ahead": 14,
    "timeout_sec": 120,
    "view": "list",            # list | week | month
    "tasks_panel": True,       # 창 왼쪽에 '기약 없는 할 일' 칸
    "show_done": True,         # 완료한 일정도 보이기 (눈 버튼)
    "auto_update": True,       # 새 버전이 나오면 알아서 받기 (updater.py)
    "x": None, "y": None,
}


# ---------------- 설정/저장 ----------------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_conf():
    old = load_json(CONF_FILE, {})
    if "model" not in old and old.get("cli_model") in MODELS:  # 예전 설정 파일 이어받기
        old["model"] = old["cli_model"]
    for k in ("cli_model", "api_model", "width", "height", "alpha", "tasks_show_done"):
        old.pop(k, None)
    conf = dict(DEFAULT_CONF)
    conf.update(old)
    save_json(CONF_FILE, conf)
    return conf


def get_tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        # tzdata가 없을 때 대비: 한국 시간 고정 오프셋
        return datetime.timezone(datetime.timedelta(hours=9))


TZ = get_tz("Asia/Seoul")


def set_tz(name):
    global TZ
    TZ = get_tz(name)


def now_local():
    return datetime.datetime.now(TZ).replace(tzinfo=None)


# ---------------- 날짜 도우미 ----------------
def valid_date(s):
    try:
        return datetime.date.fromisoformat(str(s))
    except Exception:
        return None


def valid_time(s):
    if isinstance(s, str) and re.fullmatch(r"\d{1,2}:\d{2}", s.strip()):
        h, m = map(int, s.strip().split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return datetime.time(h, m)
    return None


def dlabel(d):
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def describe(ev):
    """'11/20(금) 15:00 조합론 시험' — 반복이면 '매주 화 15:00 수업'"""
    d = valid_date(ev.get("date"))
    when = dlabel(d) if d else ""
    if ev.get("rrule"):
        when = describe_rrule(ev["rrule"], d, short=True)
    t = ev.get("time")
    return " ".join(x for x in (when, t or "", ev.get("title", "")) if x)


SPEC_KEYS = ("title", "date", "time", "end_date", "end_time", "location", "note", "rrule", "remind")


def normalize(spec):
    """일정 내용 정리: 형식을 맞추고 끝이 시작보다 앞서지 않게. 날짜나 제목이 없으면 None.
    remind: None=기본 알림, -1=알림 없음, 0=정시, N=N분 전"""
    d = valid_date(spec.get("date"))
    title = str(spec.get("title") or "").strip()
    if not d or not title:
        return None
    out = {"title": title[:200], "date": d.isoformat(), "time": None, "end_date": d.isoformat(), "end_time": None,
           "location": str(spec.get("location") or "").strip(), "note": str(spec.get("note") or "").strip(),
           "rrule": norm_rrule(spec.get("rrule")), "remind": None}
    t = valid_time(spec.get("time"))
    ed = valid_date(spec.get("end_date")) or d
    if t:
        start = datetime.datetime.combine(d, t)
        et = valid_time(spec.get("end_time"))
        end = datetime.datetime.combine(ed, et) if et else start   # 끝 시간을 안 적었으면 시작 시각만 (길이 0)
        if end < start and et and ed == d:           # 23:00~01:00 처럼 밤을 넘기는 일정
            end += datetime.timedelta(days=1)
        if end < start:
            end = start
        out.update(time=f"{t:%H:%M}", end_date=end.date().isoformat(), end_time=f"{end:%H:%M}")
    else:
        out["end_date"] = max(ed, d).isoformat()
    try:
        r = int(spec.get("remind"))
        out["remind"] = r if -1 <= r <= 40320 else None
    except (TypeError, ValueError):
        pass
    return out


def merge_changes(old, a):
    """Claude의 update 액션(바꿀 칸만 값, 나머지 null)을 기존 일정에 겹쳐 새 일정 내용을 만든다."""
    new = {k: old.get(k) for k in SPEC_KEYS}
    for k in ("title", "location", "note"):
        if a.get(k):
            new[k] = a[k]
    od, ot = valid_date(old.get("date")), valid_time(old.get("time"))
    oed, oet = valid_date(old.get("end_date")) or od, valid_time(old.get("end_time"))
    nd, ned = valid_date(a.get("date")), valid_date(a.get("end_date"))
    nt, net = valid_time(a.get("time")), valid_time(a.get("end_time"))
    if nd:
        new["date"] = nd.isoformat()
        new["end_date"] = (ned or nd + (oed - od)).isoformat()
    elif ned:
        new["end_date"] = ned.isoformat()
    if nt:
        d = valid_date(new["date"])
        if ot and oet:
            dur = datetime.datetime.combine(oed, oet) - datetime.datetime.combine(od, ot)
        else:
            dur = datetime.timedelta(0)
        start = datetime.datetime.combine(d, nt)
        end = start + dur
        new.update(time=f"{nt:%H:%M}", end_date=end.date().isoformat(), end_time=f"{end:%H:%M}")
    if net:
        new["end_time"] = f"{net:%H:%M}"
        if not nt and not ned:
            new["end_date"] = new["date"]
    if a.get("rrule"):
        new["rrule"] = a["rrule"]
    if a.get("remind") is not None:
        new["remind"] = a["remind"]
    return new


# ---------------- 반복 규칙 ----------------
def norm_rrule(rule):
    """'RRULE:FREQ=WEEKLY;BYDAY=TU' → 'FREQ=WEEKLY;BYDAY=TU' (UNTIL은 이 지역 날짜 YYYYMMDD로). 이상하면 None."""
    if not rule or not isinstance(rule, str):
        return None
    rule = rule.strip()
    if rule.upper().startswith("RRULE:"):
        rule = rule[6:]
    parts = {}
    for p in rule.split(";"):
        if "=" in p:
            k, v = p.split("=", 1)
            parts[k.strip().upper()] = v.strip().upper()
    if parts.get("FREQ") not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return None
    if "UNTIL" in parts:
        u = parts["UNTIL"]
        m = re.match(r"(\d{8})(?:T(\d{6})(Z)?)?$", u)
        if not m:
            del parts["UNTIL"]
        elif m.group(3):  # UTC 시각 → 이 지역 날짜
            dt = datetime.datetime.strptime(u[:15], "%Y%m%dT%H%M%S").replace(tzinfo=datetime.timezone.utc)
            parts["UNTIL"] = f"{dt.astimezone(TZ):%Y%m%d}"
        else:
            parts["UNTIL"] = m.group(1)
    text = ";".join(f"{k}={v}" for k, v in [("FREQ", parts.pop("FREQ"))] + sorted(parts.items()))
    try:
        from dateutil.rrule import rrulestr
        rrulestr(local_rrule(text), dtstart=datetime.datetime(2026, 1, 1))
    except ImportError:
        pass
    except Exception:
        return None
    return text


def local_rrule(rule):
    """PC 저장소 펼치기용: UNTIL 날짜를 그날 끝(23:59:59)까지로"""
    return re.sub(r"UNTIL=(\d{8})(?![T\d])", r"UNTIL=\1T235959", rule)


def google_rrule(rule, timed):
    """구글용: 시간 있는 반복은 UNTIL을 UTC 시각으로 (RFC 5545 규칙)"""
    def conv(m):
        if not timed:
            return f"UNTIL={m.group(1)}"
        d = datetime.datetime.strptime(m.group(1) + "235959", "%Y%m%d%H%M%S").replace(tzinfo=TZ)
        return f"UNTIL={d.astimezone(datetime.timezone.utc):%Y%m%dT%H%M%SZ}"
    return "RRULE:" + re.sub(r"UNTIL=(\d{8})(?![T\d])", conv, rule)


_ORD = {1: "첫째", 2: "둘째", 3: "셋째", 4: "넷째", 5: "다섯째", -1: "마지막"}


def describe_rrule(rule, start=None, short=False):
    """'FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261231' → '매주 화·목 · 12/31까지'"""
    if not rule:
        return ""
    p = dict(x.split("=", 1) for x in rule.split(";") if "=" in x)
    n = int(p.get("INTERVAL", "1") or 1)
    days = [WEEKDAYS[RR_DAYS.index(x)] for x in p.get("BYDAY", "").split(",") if x in RR_DAYS]
    f = p.get("FREQ")
    if f == "DAILY":
        s = "매일" if n == 1 else f"{n}일마다"
    elif f == "WEEKLY":
        if not days and start:
            days = [WEEKDAYS[start.weekday()]]
        s = ("매주" if n == 1 else "격주" if n == 2 else f"{n}주마다") + (" " + "·".join(days) if days else "")
    elif f == "MONTHLY":
        s = "매달" if n == 1 else f"{n}달마다"
        m = re.fullmatch(r"(-?\d)([A-Z]{2})", p.get("BYDAY", ""))
        if p.get("BYMONTHDAY"):
            s += f" {p['BYMONTHDAY']}일"
        elif m and m.group(2) in RR_DAYS:
            s += f" {_ORD.get(int(m.group(1)), m.group(1))} {WEEKDAYS[RR_DAYS.index(m.group(2))]}요일"
        elif start:
            s += f" {start.day}일"
    elif f == "YEARLY":
        s = "매년" + (f" {start.month}/{start.day}" if start else "")
    else:
        s = "반복"
    if short:
        return s
    if p.get("UNTIL"):
        u = p["UNTIL"][:8]
        s += f" · {int(u[4:6])}/{int(u[6:8])}까지"
    elif p.get("COUNT"):
        s += f" · {p['COUNT']}번"
    return s


# ---------------- 빠른 해석기 (Claude 없이) ----------------
_NEED_CLAUDE = re.compile(r"\?|취소|삭제|지워|지우|빼|미뤄|미루|당겨|바꿔|바꾸|변경|옮겨|옮기|수정|연기|뭐|무슨|언제|있어|있나|알려|보여|"
                          r"매일|매주|매달|매월|매년|마다|격주|반복|부터|까지|전에|에서|,|그리고|오늘부터")
_PFX = r"(오전|오후|아침|점심|저녁|밤|낮|새벽)"
_AM, _PM = ("오전", "아침", "새벽"), ("오후", "점심", "저녁", "밤", "낮")
_WD = "월화수목금토일"
# 시간: '3시', '3시 반', '오후 3시 20분', '15:00', '3~5시', '3시~4시반'
_TIME = r"(?:(\d{1,2}):(\d{2})|(\d{1,2})\s*시\s*(?:(반)|(\d{1,2})\s*분)?|(\d{1,2})(?=\s*[~\-]))"
# 날짜·시각 낌새: 숫자, 상대 날짜, 요일, 때를 나타내는 말, '세시' 같은 우리말 시각 → 할 일로 넣지 않고 Claude에게
_DATEISH = re.compile(r"\d|오늘|내일|낼|모레|글피|어제|그제|주말|이번\s*주|다음\s*주|담주|다다음|이번\s*달|다음\s*달|담달|월말|연말|내년|"
                      r"[월화수목금토일]요일|오전|오후|아침|점심|저녁|밤|새벽|정오|자정|"
                      r"[^가-힣](?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|열한|열두)\s*시(?![가-힣])")


def _hour(pfx, h, mi):
    """오전/오후 표시와 상식으로 24시간제 (시, 분). 애매하면 None (→ Claude에게)"""
    if h > 23 or mi > 59:
        return None
    if pfx in _AM:
        if h > 12:
            return None
        return (0 if h == 12 else h), mi
    if pfx in _PM:
        if h == 12:
            return (None if pfx == "밤" else (12, mi))
        return (h + 12 if h < 12 else h), mi
    if 1 <= h <= 6:        # 표시 없는 1~6시는 오후
        return h + 12, mi
    if h == 12 or h >= 13:
        return h, mi
    return None            # 0시, 7~11시는 오전인지 저녁인지 애매


def _soonest(h, mi, now_min, allow_tomorrow):
    """오전/오후 표시 없는 오늘의 h시 mi분 → 지금 이후로 가장 가까운 (며칠 뒤, (시, 분)).
    오전·오후 둘 다 지났으면 내일 오전 (allow_tomorrow), 아니면 오늘 오후."""
    am = h % 12
    cands = [(0, am), (0, am + 12)] + ([(1, am)] if allow_tomorrow else [])
    for off, hh in cands:
        if off * 1440 + hh * 60 + mi >= now_min:
            return off, (hh, mi)
    return 0, (am + 12, mi)


def _year_for(today, mo, d):
    try:
        x = datetime.date(today.year, mo, d)
    except ValueError:
        return None
    if x < today - datetime.timedelta(days=60):   # 두 달보다 더 지난 날짜면 내년
        try:
            x = datetime.date(today.year + 1, mo, d)
        except ValueError:
            return None
    return x


def quick_parse(text, today, now_min=None):
    """'11/20 금 3시 조합론 시험', '다음주 화 PAE 에세이 마감' 같은 단순 등록만 바로 해석.
    · 날짜도 시각도 없으면 ('과제 마감') → 날짜 없는 할 일 {"op": "task"}
    · 날짜 없이 시각만 있으면 ('10시 과제마감') → 오늘. 오전/오후 표시가 없으면 지금 이후로 가장 가까운 쪽
      (now_min: 지금 시각, 0시부터 센 분. 둘 다 지났으면 내일 오전)
    조금이라도 애매하면 None을 돌려줘 Claude에게 넘긴다."""
    s = " " + text.strip() + " "
    if len(s) > 60 or _NEED_CLAUDE.search(s):
        return None
    if now_min is None:
        now = now_local()
        now_min = now.hour * 60 + now.minute
    dates = []   # (시작, 끝, 날짜)
    for m in re.finditer(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s):
        try:
            dates.append((m.start(), m.end(), datetime.date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            return None
    for m in re.finditer(r"(?<![\d/.\-])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])", s):
        dates.append((m.start(), m.end(), _year_for(today, int(m[1]), int(m[2]))))
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일", s):
        dates.append((m.start(), m.end(), _year_for(today, int(m[1]), int(m[2]))))
    for m in re.finditer(r"(오늘|내일|모레|글피)", s):
        dates.append((m.start(), m.end(), today + datetime.timedelta(days=("오늘", "내일", "모레", "글피").index(m[1]))))
    monday = today - datetime.timedelta(days=today.weekday())
    for m in re.finditer(rf"(이번\s*주|다음\s*주|담주|다다음\s*주)\s*([{_WD}])(?:요일)?(?![가-힣])", s):
        wk = 2 if m[1].startswith("다다음") else (0 if m[1].startswith("이번") else 1)
        d = monday + datetime.timedelta(days=7 * wk + _WD.index(m[2]))
        dates.append((m.start(), m.end(), d if d >= today else None))
    if not dates:
        m = re.search(rf"(?<![가-힣])([{_WD}])요일", s)
        if m:
            ahead = (_WD.index(m[1]) - today.weekday()) % 7
            if ahead == 0:      # 오늘과 같은 요일이면 이번 주인지 다음 주인지 애매
                return None
            dates.append((m.start(), m.end(), today + datetime.timedelta(days=ahead)))
    undated = not dates
    if undated:
        if not re.search(_TIME, s):   # 날짜도 시각도 없음 → 날짜 없는 할 일
            if _DATEISH.search(s):     # '다음주 과제', '저녁 약속'처럼 때를 나타내는 말이 있으면 Claude에게
                return None
            title = re.sub(r"\s+", " ", s).strip(" .·")
            return {"op": "task", "title": title[:300]} if re.search(r"[가-힣A-Za-z]", title) else None
        day, rest = today, s
    else:
        if len(dates) != 1 or dates[0][2] is None:
            return None
        a, b, day = dates[0]
        rest = s[:a] + " " + s[b:]
        # 날짜 바로 뒤의 요일 표시('11/20 금', '11/20(금)')는 맞는지 확인만
        mw = re.match(rf"\s*\(?([{_WD}])(?:요일)?\)?(?![가-힣])", s[b:])
        if mw and not re.match(r"(이번|다음|담주|다다음)", s[a:b]):
            if _WD.index(mw[1]) != day.weekday():
                return None   # 날짜와 요일이 안 맞으면 Claude가 확인해서 알려 줌
            rest = s[:a] + " " + s[b + mw.end():]
    if re.search(r"(?<!\d)\d{1,2}\s*일(?!정)", rest):   # '20일' 같은 다른 날짜가 또 있음
        return None

    times = []   # (시작, 끝, 오전/오후, 시, 분, 숫자만 있음)
    for m in re.finditer(rf"{_PFX}?\s*{_TIME}", rest):
        h = int(m[2] or m[4] or m[7])
        mi = int(m[3] or (30 if m[5] else (m[6] or 0)))
        pfx = m[1]
        if not pfx and m[2] and len(m[2]) == 2 and h < 12:   # '09:30'처럼 두 자리로 쓴 24시간제
            pfx = "오전"
        times.append((m.start(), m.end(), pfx, h, mi, bool(m[7])))
    # 오늘 일정(날짜 없이 적었거나 '오늘')의 표시 없는 1~12시 → 지금 이후로 가장 가까운 오전/오후
    soon, off = undated or day == today, 0

    def hour(pfx, h, mi):
        nonlocal off
        if soon and not pfx and 1 <= h <= 12 and mi <= 59:
            off, hm = _soonest(h, mi, now_min, undated)
            return hm
        return _hour(pfx, h, mi)
    start = end = None
    if len(times) == 1:
        start = None if times[0][5] else hour(times[0][2], times[0][3], times[0][4])
        if not start:
            return None
    elif len(times) == 2 and not times[1][5] and re.fullmatch(r"\s*[~\-]\s*", rest[times[0][1]:times[1][0]]):
        p0 = times[0][2] or times[1][2]
        start = hour(p0, times[0][3], times[0][4])
        if not start:
            return None
        eh, emi = times[1][3], times[1][4]
        if times[1][2]:
            end = _hour(times[1][2], eh, emi)
        else:
            if start[0] >= 12 and eh < 12:
                eh += 12
            end = (eh, emi) if eh < 24 else None
        if not end or end <= start:
            return None
    elif times:
        return None
    for tm in reversed(times):
        rest = rest[:tm[0]] + " " + rest[tm[1]:]
    if re.search(r"\d\s*(시|분|일|월|:|/|~)", rest):   # 남은 시간·날짜 조각이 있으면 애매
        return None
    if undated and _DATEISH.search(rest):   # '다음주 3시 회의'처럼 날짜 낌새가 남아 있으면 Claude에게
        return None
    title = re.sub(r"(?<!\S)(에는|에|은|는|날|쯤|경|[~\-])(?!\S)", " ", rest)
    title = re.sub(r"\s+", " ", title).strip(" .·")
    if not re.search(r"[가-힣A-Za-z]", title):
        return None
    day += datetime.timedelta(days=off)
    return {"op": "add", "title": title, "date": day.isoformat(),
            "time": f"{start[0]:02d}:{start[1]:02d}" if start else None,
            "end_time": f"{end[0]:02d}:{end[1]:02d}" if end else None,
            "end_date": None, "location": "", "note": "", "rrule": None, "remind": None}


# ---------------- Claude 호출 ----------------
def find_claude():
    p = shutil.which("claude")
    if p:
        return p
    home = os.path.expanduser("~")
    for c in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")):
        if os.path.exists(c):
            return c
    return None


_cli_logged_out = False   # claude 명령이 '로그인 안 됨'이라고 하면 이번 실행 동안은 안 쓴다


def engine_name(conf):
    """지금 쓸 Claude 연결: 'api' | 'cli' | None"""
    e = conf.get("engine", "auto")
    if e in ("auto", "api") and conf.get("api_key"):
        return "api"
    if e in ("auto", "cli") and not _cli_logged_out and find_claude():
        return "cli"
    return None


_NULL_STR = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["add", "task", "update", "delete", "query"]},
                "sid": _NULL_STR, "scope": _NULL_STR,
                "title": _NULL_STR, "date": _NULL_STR, "time": _NULL_STR,
                "end_date": _NULL_STR, "end_time": _NULL_STR,
                "location": _NULL_STR, "note": _NULL_STR, "rrule": _NULL_STR,
                "remind": {"type": ["integer", "null"]},
                "from": _NULL_STR, "to": _NULL_STR,
            },
            "required": ["op", "sid", "scope", "title", "date", "time", "end_date", "end_time",
                         "location", "note", "rrule", "remind", "from", "to"],
            "additionalProperties": False,
        }},
        "reply": {"type": "string"},
    },
    "required": ["actions", "reply"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """너는 사용자의 캘린더 비서다. 사용자가 대충 적은 한 줄을 읽고 일정 작업(actions)과 짧은 답(reply)을 JSON으로 낸다.

작업 종류(op):
- add: 새 일정. title·date 필수, 모르는 칸은 null.
- task: 날짜 없는 할 일(구글 할 일)로 넣기. title만 채우고 나머지는 null.
- update: 기존 일정 바꾸기. sid 필수. 바꿀 칸만 채우고 나머지는 null.
- delete: 기존 일정 지우기. sid 필수.
- query: 일정 묻기("11월 20일에 뭐 있어?", "이번주 일정"). from·to 필수(하루면 둘 다 같은 날).

칸 설명:
- date, end_date: YYYY-MM-DD. time, end_time: HH:MM(24시간). 시간 없이 날짜만 있으면 time=null(종일 일정).
- 끝 시간은 사용자가 적었을 때만. 모르면 end_time=null(앱이 시작 시각만 넣음 — 길이를 지어내지 말 것). 여러 날짜에 걸친 종일 일정은 end_date.
- location: 장소가 보이면("강남에서", "302호"). note: 그 밖의 부가 정보. 없으면 null.
- rrule: 반복 일정이면 RFC 5545 RRULE 본문(접두어 없이). 예) 매주 화목 → "FREQ=WEEKLY;BYDAY=TU,TH", 매달 25일 → "FREQ=MONTHLY;BYMONTHDAY=25", 격주 → "FREQ=WEEKLY;INTERVAL=2;BYDAY=..", 12월까지 → 끝에 ";UNTIL=20261231", 10번 → ";COUNT=10". 반복이면 date는 첫 번째 날짜.
- remind: "30분 전에 알려줘" 같은 알림 요청이 있을 때만 분 단위 정수(1시간 전=60, 하루 전=1440, 정각=0). 없으면 null.
- scope: 반복 일정(repeat=true)을 update/delete할 때만. "이번만", "이번주", 특정 날짜 하나 → "one". "매주 수업 없애줘", "반복 전체" → "all". 애매하면 "one". 반복이 아니면 null.
- sid: 기존 일정 목록의 sid. 목록에 없는 일정은 고칠 수 없다.

규칙:
- 일정 등록이 기본이다. 짧게 적어도 등록한다. 한 입력에 일정이 여러 개면 add를 여러 개.
- 상대 날짜(내일, 다음주 화요일, 이번주 금요일)는 날짜표를 보고 정확한 날짜로 바꾼다. 한 주는 월요일에 시작한다. 요일과 날짜가 안 맞으면 날짜를 따르고 reply에서 알려 준다.
- 날짜도 시각도 없는 등록("과제 마감", "책 반납")은 task로 넣는다. 되묻지 않는다.
- 날짜 없이 시각만 있으면 오늘 일정이다("10시 과제마감" → 오늘 10시 또는 22시).
- 오늘 일정의 시각에 오전/오후 표시가 없으면 지금 이후로 가장 가까운 쪽을 고른다(지금 11:00에 "10시" → 22:00, 지금 09:00에 "10시" → 10:00). 날짜 없이 적었는데 오전·오후 둘 다 지났으면 내일 오전.
- 다른 날짜의 시각에 오전/오후 표시가 없으면 상식으로 판단한다(수업·약속 1~6시는 오후, 저녁 모임·회식 7~11시는 오후, 수업·출근 7~11시는 오전).
- "~까지", "마감"은 그 날짜(시각이 있으면 그 시각)의 일정. 날짜도 시각도 없으면 task.
- 바꿀 대상이 애매하면 actions를 비우고 reply로 되묻는다.
- 일정과 상관없는 말이면 actions를 비우고 reply로 짧게 답한다.
- reply는 한국어 한 문장으로 짧게. 예: "11/20(금) 15:00 조합론 시험 등록했어요", "할 일에 넣었어요: 과제 마감"."""

CLI_HINT = """

출력은 아래 모양의 JSON 객체 하나만. 코드블록·설명 금지. 도구나 파일은 쓰지 마라.
{"actions": [{"op": "add", "sid": null, "scope": null, "title": "...", "date": "YYYY-MM-DD", "time": "HH:MM 또는 null", "end_date": null, "end_time": null, "location": null, "note": null, "rrule": null, "remind": null, "from": null, "to": null}], "reply": "..."}"""


def build_user(user_text, events, now):
    """날짜표 + 가까운 일정 + 입력. events: [{sid,title,date,time,...}]"""
    today = now.date()
    monday = today - datetime.timedelta(days=today.weekday())
    weeks = " | ".join(f"{name} {dlabel(monday + datetime.timedelta(days=7 * i))}~{dlabel(monday + datetime.timedelta(days=7 * i + 6))}"
                       for i, name in enumerate(("이번주", "다음주", "다다음주")))
    names = {0: "=오늘", 1: "=내일", 2: "=모레"}
    days = " ".join(f"{dlabel(today + datetime.timedelta(days=i))}{names.get(i, '')}" for i in range(0, 35))
    return (f"현재: {now:%Y-%m-%d} ({WEEKDAYS[today.weekday()]}) {now:%H:%M}, 올해는 {today.year}년\n"
            f"주: {weeks}\n날짜표: {days}\n"
            f"기존 일정: {json.dumps(events, ensure_ascii=False)}\n"
            f"사용자 입력: {json.dumps(user_text, ensure_ascii=False)}")


def call_claude_cli(claude_path, prompt, model, timeout, cwd):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # 본인 Claude 구독 로그인으로만 실행
    cmd = [claude_path, "-p", "stdin으로 받은 지시를 따르고 JSON 객체 하나만 출력해.",
           "--model", model, "--max-turns", "1"]
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=cwd, env=env, creationflags=(0x08000000 if IS_WIN else 0),
    )
    out = (proc.stdout or "").strip()
    if "not logged in" in (out + (proc.stderr or "")).lower():
        global _cli_logged_out
        _cli_logged_out = True
        raise RuntimeError("Claude Code에 로그인되어 있지 않아요. ⋯ → 설정에서 API 키를 넣어 주세요"
                           " (또는 터미널에서 claude 실행 → /login).")
    if proc.returncode != 0 and not out:
        raise RuntimeError((proc.stderr or "claude 실행 실패").strip()[:300])
    return out


def api_client(conf):
    import anthropic
    return anthropic.Anthropic(api_key=conf["api_key"], timeout=conf.get("timeout_sec", 120), max_retries=2)


def friendly_api_error(e):
    """anthropic 예외 → 사용자에게 보여 줄 한국어"""
    import anthropic
    if isinstance(e, anthropic.AuthenticationError):
        return "API 키가 맞지 않아요. ⋯ → 설정에서 키를 다시 확인해 주세요."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "이 API 키로는 이 모델을 쓸 수 없어요. 설정에서 다른 모델을 골라 보세요."
    if isinstance(e, anthropic.NotFoundError):
        return "모델을 찾지 못했어요. 설정에서 다른 모델을 골라 보세요."
    if isinstance(e, anthropic.RateLimitError):
        return "요청이 잠깐 너무 많아요. 조금 뒤에 다시 해 주세요."
    if isinstance(e, anthropic.BadRequestError):
        msg = str(getattr(e, "message", e))
        if "credit" in msg.lower() or "balance" in msg.lower():
            return "API 크레딧이 부족해요. Anthropic 콘솔의 Billing에서 확인해 주세요."
        return "요청 오류: " + msg[:160]
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude 서버 오류({e.status_code}). 조금 뒤에 다시 해 주세요."
    if isinstance(e, anthropic.APIConnectionError):
        return "Claude에 연결하지 못했어요. 인터넷 연결을 확인해 주세요."
    return str(e)[:200]


def call_claude_api(conf, system, user):
    import anthropic
    client = api_client(conf)
    model = MODELS.get(conf.get("model"), conf.get("model") or MODELS["sonnet"])
    kw = dict(model=model, max_tokens=8000, system=system,
              messages=[{"role": "user", "content": user}],
              # 짧은 해석이라 effort는 low, 응답은 스키마에 맞는 JSON으로 고정
              output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
    try:
        if model.startswith(("claude-sonnet", "claude-opus")):
            # 안전 분류기가 오판해 거절하면 서버가 알맞은 다른 모델로 다시 처리 (Haiku는 지원 안 함)
            msg = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
        else:
            msg = client.messages.create(**kw)
    except anthropic.APIError as e:
        raise RuntimeError(friendly_api_error(e)) from e
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude가 이 요청은 처리하지 않았어요. 다르게 적어 주세요.")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("한 번에 처리하기엔 내용이 너무 많아요. 나눠서 적어 주세요.")
    return "".join(b.text for b in msg.content if b.type == "text")


def test_api_key(conf):
    """토큰을 쓰지 않는 모델 정보 조회로 키가 맞는지만 확인. 문제 없으면 None, 아니면 안내 문구"""
    import anthropic
    try:
        api_client(conf).models.retrieve(MODELS.get(conf.get("model"), MODELS["sonnet"]))
        return None
    except anthropic.APIError as e:
        return friendly_api_error(e)


def parse_response(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Claude 응답을 해석하지 못했어요: " + text[:150])
    data = json.loads(text[start:end + 1])
    acts = data.get("actions")
    data["actions"] = [a for a in acts if isinstance(a, dict)] if isinstance(acts, list) else []
    data["reply"] = str(data.get("reply") or "")
    return data


def ask_claude(conf, user_text, events, now, cwd):
    eng = engine_name(conf)
    user = build_user(user_text, events, now)
    if eng == "api":
        return parse_response(call_claude_api(conf, SYSTEM_PROMPT, user))
    if eng == "cli":
        return parse_response(call_claude_cli(find_claude(), SYSTEM_PROMPT + CLI_HINT + "\n\n" + user,
                                              conf.get("model", "sonnet"), conf["timeout_sec"], cwd))
    raise RuntimeError("Claude 연결이 없어요. ⋯ → 설정에서 API 키를 넣거나 Claude Code를 설치해 주세요.")


# ---------------- 해석 → 할 일 목록 ----------------
def brief_events(near):
    """Claude에게 보여 줄 짧은 일정 목록과 sid → 일정 표"""
    brief, sid_map = [], {}
    for i, ev in enumerate(near):
        sid = f"e{i}"
        sid_map[sid] = ev
        b = {"sid": sid, "title": ev["title"], "date": ev["date"], "time": ev["time"]}
        if ev.get("end_time") and (ev["end_time"] != ev.get("time") or ev.get("end_date") != ev.get("date")):   # 시작 시각만 있는 일정은 끝을 안 보낸다
            b["end_time"] = ev["end_time"]
        if ev.get("location"):
            b["loc"] = ev["location"]
        if ev.get("recurring"):
            b["repeat"] = True
        if ev.get("done"):
            b["done"] = True
        brief.append(b)
    return brief, sid_map


def make_plan(data, sid_map, store):
    """Claude(또는 빠른 해석기) 결과 → [{op, id, scope, spec, desc}] 와 query 범위"""
    plan, query = [], None
    for a in data["actions"]:
        op = a.get("op")
        if op == "add":
            spec = normalize(a)
            if spec:
                plan.append({"op": "add", "spec": spec, "desc": "추가 · " + describe(spec)})
        elif op == "task":   # 날짜 없는 할 일 (왼쪽 할 일 칸 · 구글 할 일)
            title = " ".join(str(a.get("title") or "").split())[:300]
            if title:
                plan.append({"op": "task", "title": title, "desc": "할 일 · " + title})
        elif op in ("update", "delete") and a.get("sid") in sid_map:
            ev = sid_map[a["sid"]]
            scope = "all" if (ev.get("recurring") and a.get("scope") == "all") else "one"
            tail = " (반복 전체)" if scope == "all" else ""
            if op == "delete":
                shown = store.get(ev["id"]) if scope == "all" else ev   # 반복 전체면 '매주 화·목 …'으로 보여 주기
                plan.append({"op": "delete", "id": ev["id"], "scope": scope, "desc": "삭제 · " + describe(shown) + tail})
            else:
                old = store.get(ev["id"])
                if scope == "one":
                    old["rrule"] = None
                spec = normalize(merge_changes(old, a))
                if spec:
                    plan.append({"op": "update", "id": ev["id"], "scope": scope, "spec": spec,
                                 "desc": f"변경 · {describe(ev)} → {describe(spec)}{tail}"})
        elif op == "query":
            f, t = valid_date(a.get("from")), valid_date(a.get("to"))
            if f:
                query = {"from": f.isoformat(), "to": (t if t and t >= f else f).isoformat()}
    return plan, query


def execute_plan(store, plan):
    """할 일 실행 → 되돌리기 기록 묶음 (실패하면 그때까지 한 것을 되돌리고 오류)"""
    batch = []
    try:
        for p in plan:
            if p["op"] == "add":
                _, rec = store.add(p["spec"])
            elif p["op"] == "task":
                rec = store.add_task(p["title"])
            elif p["op"] == "update":
                rec = store.update(p["id"], p["spec"], p["scope"])
            else:
                rec = store.delete(p["id"], p["scope"])
            batch.append(rec)
    except Exception:
        undo_batch(store, batch)
        raise
    return batch


def undo_batch(store, batch):
    for rec in reversed(batch):
        store.undo(rec)
