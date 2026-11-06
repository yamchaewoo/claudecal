# -*- coding: utf-8 -*-
"""일정 저장소 두 가지 — 같은 모양으로 쓴다.
  · LocalStore : 이 PC의 %APPDATA%\\ClaudeCal\\events.json (구글 연결 전)
  · GoogleStore: 구글 캘린더 '기본 캘린더' (token.json이 있으면)

화면·계산에 쓰는 일정 모양:
  {id, series_id, title, date, time, end_date, end_time, location, note, remind, recurring, done, link, source}
  date/end_date는 'YYYY-MM-DD'(끝 날짜 포함), time/end_time은 'HH:MM' 또는 None(종일), done은 완료 체크
바꾸는 함수(add/update/delete/set_done)는 되돌리기 기록(list)을 돌려주고, undo(기록)으로 되돌린다.

기약 없는 할 일: {id, title, state('todo'|'done'), notes(메모), done_at}
  · LocalStore: tasks.json / GoogleStore: 구글 할 일(Tasks)의 기본 목록
  · add_task / set_task / delete_task / clear_done도 되돌리기 기록을 돌려준다.
"""
import os
import copy
import uuid
import datetime

import calcore as core

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
CALLIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist"   # 구글 캘린더 기본 알림 바꾸기
SCOPES = ["https://www.googleapis.com/auth/calendar.events", TASKS_SCOPE, CALLIST_SCOPE]
DONE_KEY = "claudecalDone"   # 구글 일정의 숨은 칸(extendedProperties.private)에 완료 체크를 "1"/"0"으로
DOING = "▶ "                 # 예전 '진행 중' 표시 — 이제 없는 기능이라 읽을 때 떼고, 고칠 때 지운다
TASK_STATES = ("todo", "done")


def _task_order(tasks):
    """할 일(최근 것 위) → 완료(최근에 끝낸 것 위)"""
    active = sorted((t for t in tasks if t["state"] != "done"), key=lambda t: t.get("pos", ""))
    done = sorted((t for t in tasks if t["state"] == "done"), key=lambda t: t.get("done_at") or "", reverse=True)
    return active + done[:50]


def _d(s):
    return datetime.date.fromisoformat(s)


def _sort_key(ev):
    return (ev["date"], ev["time"] is not None, ev["time"] or "", ev["title"])


# ---------------- 이 PC ----------------
class LocalStore:
    kind = "local"

    def __init__(self, path):
        self.path = path
        self.events = core.load_json(path, {}).get("events", [])
        self.tasks_path = os.path.join(os.path.dirname(path), "tasks.json")
        self.tasks = core.load_json(self.tasks_path, {}).get("tasks", [])

    def label(self):
        return "이 PC"

    def count(self):
        return len(self.events)

    def _save(self):
        core.save_json(self.path, {"version": 1, "events": self.events})

    def _find(self, mid):
        return next((m for m in self.events if m["id"] == mid), None)

    @staticmethod
    def _split(eid):
        """반복 일정의 한 회차 id는 '원본id__YYYYMMDD'"""
        if "__" in eid:
            mid, d = eid.split("__", 1)
            return mid, f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return eid, None

    def _unified(self, m, day=None):
        d0 = _d(m["date"])
        span = _d(m["end_date"]) - d0
        day = day or d0
        rec = bool(m.get("rrule"))
        return {"id": f"{m['id']}__{day:%Y%m%d}" if rec else m["id"], "series_id": m["id"] if rec else None,
                "title": m["title"], "date": day.isoformat(), "time": m.get("time"),
                "end_date": (day + span).isoformat(), "end_time": m.get("end_time"),
                "location": m.get("location", ""), "note": m.get("note", ""), "remind": m.get("remind"),
                "recurring": rec, "link": "", "source": "local",
                "done": day.isoformat() in m.get("done_dates", []) if rec else bool(m.get("done"))}

    def _occurrences(self, m, f, t):
        from dateutil.rrule import rrulestr
        start = datetime.datetime.combine(_d(m["date"]), core.valid_time(m.get("time")) or datetime.time(0))
        ex = set(m.get("exdates", []))
        rule = rrulestr(core.local_rrule(m["rrule"]), dtstart=start)
        for dt in rule.between(datetime.datetime.combine(f, datetime.time(0)),
                               datetime.datetime.combine(t, datetime.time(23, 59, 59)), inc=True):
            if dt.date().isoformat() not in ex:
                yield dt.date()

    def list_range(self, f, t):
        out = []
        for m in self.events:
            d0 = _d(m["date"])
            span = _d(m["end_date"]) - d0
            if not m.get("rrule"):
                if d0 <= t and d0 + span >= f:
                    out.append(self._unified(m))
                continue
            for day in self._occurrences(m, f - span, t):
                out.append(self._unified(m, day))
        return sorted(out, key=_sort_key)

    def get(self, eid):
        mid, day = self._split(eid)
        m = self._find(mid)
        if not m:
            raise KeyError("일정을 찾지 못했어요 (이미 지워졌을 수 있어요).")
        u = self._unified(m, _d(day) if day else None)
        u["rrule"] = m.get("rrule")
        u["rrule_text"] = core.describe_rrule(m.get("rrule"), _d(m["date"]))
        return u

    def add(self, spec):
        m = {"id": uuid.uuid4().hex[:12], **{k: spec.get(k) for k in core.SPEC_KEYS}, "exdates": []}
        self.events.append(m)
        self._save()
        return m["id"], [{"t": "remove", "id": m["id"]}]

    def update(self, eid, spec, scope):
        mid, day = self._split(eid)
        m = self._find(mid)
        if not m:
            raise KeyError("일정을 찾지 못했어요 (이미 지워졌을 수 있어요).")
        old = copy.deepcopy(m)
        undo = [{"t": "replace", "id": mid, "old": old}]
        fields = {k: spec.get(k) for k in core.SPEC_KEYS}
        if day and scope == "one":            # 이 회차만: 반복에서 빼고 따로 하나 만든다
            m.setdefault("exdates", []).append(day)
            new = {"id": uuid.uuid4().hex[:12], **fields, "rrule": None, "exdates": [],
                   "done": day in m.get("done_dates", [])}
            self.events.append(new)
            undo.append({"t": "remove", "id": new["id"]})
        elif day and fields["rrule"]:         # 반복 전체: 고른 회차에서 옮긴 만큼 시작일도 옮긴다
            shift = _d(fields["date"]) - _d(day)
            span = _d(fields["end_date"]) - _d(fields["date"])
            start = _d(m["date"]) + shift
            m.update(fields, date=start.isoformat(), end_date=(start + span).isoformat())
        else:                                 # 단일 일정, 또는 반복을 끈 경우
            m.update(fields)
            if not fields["rrule"]:
                m["exdates"] = []
        self._save()
        return undo

    def delete(self, eid, scope):
        mid, day = self._split(eid)
        m = self._find(mid)
        if not m:
            raise KeyError("일정을 찾지 못했어요 (이미 지워졌을 수 있어요).")
        if day and scope == "one":
            old = copy.deepcopy(m)
            m.setdefault("exdates", []).append(day)
            undo = [{"t": "replace", "id": mid, "old": old}]
        else:
            self.events.remove(m)
            undo = [{"t": "put", "ev": copy.deepcopy(m)}]
        self._save()
        return undo

    def set_done(self, eid, done):
        """완료 체크. 반복 일정은 회차마다 따로 (done_dates)"""
        mid, day = self._split(eid)
        m = self._find(mid)
        if not m:
            raise KeyError("일정을 찾지 못했어요 (이미 지워졌을 수 있어요).")
        old = copy.deepcopy(m)
        if day:
            dates = set(m.get("done_dates", []))
            if done:
                dates.add(day)
            else:
                dates.discard(day)
            m["done_dates"] = sorted(dates)
        else:
            m["done"] = bool(done)
        self._save()
        return [{"t": "replace", "id": mid, "old": old}]

    def undo(self, records):
        for r in reversed(records):
            if r["t"] == "remove":
                self.events = [m for m in self.events if m["id"] != r["id"]]
            elif r["t"] == "replace":
                self.events = [m for m in self.events if m["id"] != r["id"]] + [copy.deepcopy(r["old"])]
            elif r["t"] == "put" and not self._find(r["ev"]["id"]):
                self.events.append(copy.deepcopy(r["ev"]))
            elif r["t"] == "task_remove":
                self.tasks = [t for t in self.tasks if t["id"] != r["id"]]
            elif r["t"] in ("task_replace", "task_put"):
                self.tasks = [t for t in self.tasks if t["id"] != r["task"]["id"]] + [copy.deepcopy(r["task"])]
        self._save()
        self._save_tasks()

    def masters(self):
        return copy.deepcopy(self.events)

    def clear(self, backup_path):
        core.save_json(backup_path, {"version": 1, "events": self.events, "tasks": self.tasks})
        self.events = []
        self.tasks = []
        self._save()
        self._save_tasks()

    # ----- 기약 없는 할 일 -----
    def _save_tasks(self):
        core.save_json(self.tasks_path, {"version": 1, "tasks": self.tasks})

    def _task(self, tid):
        t = next((t for t in self.tasks if t["id"] == tid), None)
        if not t:
            raise KeyError("할 일을 찾지 못했어요 (이미 지워졌을 수 있어요).")
        return t

    def list_tasks(self):
        def pos(t):   # 최근에 만든 게 위로 (구글 할 일처럼 작은 값이 위)
            try:
                us = int(datetime.datetime.fromisoformat(t.get("created", "")).timestamp() * 1e6)
            except ValueError:
                us = 0
            return f"{10 ** 17 - us:018d}"
        return _task_order([{"notes": "", **t, "state": "done" if t["state"] == "done" else "todo", "pos": pos(t)}
                            for t in copy.deepcopy(self.tasks)])   # 예전 '진행 중'은 그냥 할 일로

    def raw_tasks(self):
        return copy.deepcopy(self.tasks)

    def get_task(self, tid):
        return next(t for t in self.list_tasks() if t["id"] == self._task(tid)["id"])

    def add_task(self, title, notes="", state="todo"):
        t = {"id": uuid.uuid4().hex[:12], "title": title, "state": state, "notes": notes or "",
             "done_at": datetime.datetime.now().isoformat(timespec="seconds") if state == "done" else None,
             "created": datetime.datetime.now().isoformat(timespec="microseconds")}
        self.tasks.append(t)
        self._save_tasks()
        return [{"t": "task_remove", "id": t["id"]}]

    def set_task(self, tid, state):
        t = self._task(tid)
        old = copy.deepcopy(t)
        t["state"] = state
        t["done_at"] = datetime.datetime.now().isoformat(timespec="seconds") if state == "done" else None
        self._save_tasks()
        return [{"t": "task_replace", "task": old}]

    def edit_task(self, tid, title, notes):
        t = self._task(tid)
        old = copy.deepcopy(t)
        t.update(title=title, notes=notes)
        self._save_tasks()
        return [{"t": "task_replace", "task": old}]

    def delete_task(self, tid):
        t = self._task(tid)
        self.tasks.remove(t)
        self._save_tasks()
        return [{"t": "task_put", "task": t}]

    def clear_done(self):
        done = [t for t in self.tasks if t["state"] == "done"]
        self.tasks = [t for t in self.tasks if t["state"] != "done"]
        self._save_tasks()
        return [{"t": "task_put", "task": t} for t in done]


# ---------------- 구글 캘린더 ----------------
class NeedLogin(Exception):
    pass


def _rfc(dt_naive):
    return dt_naive.replace(tzinfo=core.TZ).isoformat()


def to_google(spec, tz_name, patch=False, recurrence=True):
    """일정 내용 → 구글 이벤트 body. patch=True면 비운 칸은 null로 보내 지운다."""
    body = {"summary": spec["title"]}
    for k, g in (("location", "location"), ("note", "description")):
        if spec.get(k):
            body[g] = spec[k]
        elif patch:
            body[g] = None
    d, ed = _d(spec["date"]), _d(spec["end_date"])
    timed = bool(spec.get("time"))
    if timed:
        st = datetime.datetime.combine(d, core.valid_time(spec["time"]))
        en = datetime.datetime.combine(ed, core.valid_time(spec["end_time"]))
        body["start"] = {"dateTime": st.isoformat(), "timeZone": tz_name}
        body["end"] = {"dateTime": en.isoformat(), "timeZone": tz_name}
        if patch:
            body["start"]["date"] = body["end"]["date"] = None
    else:
        body["start"] = {"date": d.isoformat()}
        body["end"] = {"date": (ed + datetime.timedelta(days=1)).isoformat()}  # 종일 일정은 끝 날짜 미포함
        if patch:
            for x in (body["start"], body["end"]):
                x.update(dateTime=None, timeZone=None)
    r = spec.get("remind")
    if r is None and not timed:      # 종일 일정은 기본 = 알림 없음 (구글 기본값을 따르면 전날 밤 11시 반 같은 때 울린다)
        body["reminders"] = {"useDefault": False, "overrides": []}
    elif r is None:                  # 시간 있는 일정 = 구글 캘린더 기본 알림을 따른다 (위젯 설정과 같은 값)
        body["reminders"] = {"useDefault": True}
    else:
        body["reminders"] = {"useDefault": False,
                             "overrides": [] if r < 0 else [{"method": "popup", "minutes": r}]}
    if recurrence and spec.get("rrule"):
        body["recurrence"] = [core.google_rrule(spec["rrule"], timed)]
    return body


# 구글 캘린더에서 일정마다 고르는 색 (colorId → 구글 캘린더 화면에 보이는 색). 안 골랐으면 None = 위젯 기본색
GOOGLE_COLORS = {"1": "#7986cb", "2": "#33b679", "3": "#8e24aa", "4": "#e67c73", "5": "#f6bf26", "6": "#f4511e",
                 "7": "#039be5", "8": "#616161", "9": "#3f51b5", "10": "#0b8043", "11": "#d50000"}


def from_google(ev, tz):
    s, e = ev.get("start", {}), ev.get("end", {})
    out = {"id": ev["id"], "series_id": ev.get("recurringEventId"), "title": ev.get("summary") or "(제목 없음)",
           "location": ev.get("location", ""), "note": ev.get("description", ""), "link": ev.get("htmlLink", ""),
           "recurring": bool(ev.get("recurringEventId") or ev.get("recurrence")), "source": "google",
           "color": GOOGLE_COLORS.get(str(ev.get("colorId") or "")),
           "time": None, "end_time": None,
           "done": ((ev.get("extendedProperties") or {}).get("private") or {}).get(DONE_KEY) == "1"}
    if "dateTime" in s:
        st = datetime.datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(tz)
        en = datetime.datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")).astimezone(tz)
        out.update(date=st.date().isoformat(), time=f"{st:%H:%M}", end_date=en.date().isoformat(), end_time=f"{en:%H:%M}")
    else:
        sd = _d(s["date"])
        ed = _d(e["date"]) - datetime.timedelta(days=1) if e.get("date") else sd
        out.update(date=sd.isoformat(), end_date=max(sd, ed).isoformat())
    rem = ev.get("reminders") or {}
    if rem.get("useDefault", True):
        out["remind"] = None
    else:
        mins = [o["minutes"] for o in rem.get("overrides", []) if o.get("method") == "popup"]
        out["remind"] = min(mins) if mins else (None if not out["time"] else -1)   # 알림 없는 종일 일정 = 기본값
    return out


def popup_min(reminders):
    """구글 알림 목록 → 가장 이른 팝업 알림 분 (없으면 -1)"""
    mins = [o.get("minutes", 0) for o in (reminders or []) if o.get("method") == "popup"]
    return min(mins) if mins else -1


def _snapshot(ev, recurrence=True):
    """되돌리기용: 구글 이벤트의 바꿀 수 있는 칸을 통째로 (patch로 되돌릴 수 있게 빈 칸은 None)"""
    snap = {"summary": ev.get("summary"), "location": ev.get("location"), "description": ev.get("description"),
            "reminders": ev.get("reminders") or {"useDefault": True}}
    for k in ("start", "end"):
        x = ev.get(k, {})
        snap[k] = {"date": x.get("date"), "dateTime": x.get("dateTime"), "timeZone": x.get("timeZone")}
    if recurrence and ev.get("recurrence"):
        snap["recurrence"] = ev["recurrence"]
    return snap


class NeedTasksScope(Exception):
    """구글 할 일 권한이 없을 때 (예전에 캘린더 권한만 받은 연결)"""


class NeedCalListScope(Exception):
    """구글 캘린더 기본 알림을 바꿀 권한이 없을 때 (예전 연결)"""


TASKS_SCOPE_MSG = "할 일 목록을 쓰려면 구글을 한 번 다시 연결해 주세요 (할 일 권한이 새로 필요해요)."


def _has_scope(creds, scope):
    return scope in (getattr(creds, "granted_scopes", None) or creds.scopes or [])


def _has_tasks(creds):
    return _has_scope(creds, TASKS_SCOPE)


def task_from_google(t):
    title = t.get("title") or ""
    return {"id": t["id"], "title": title[len(DOING):] if title.startswith(DOING) else title,
            "state": "done" if t.get("status") == "completed" else "todo", "notes": t.get("notes") or "",
            "done_at": t.get("completed"), "pos": t.get("position", "")}


def _done_body(done):
    return {"extendedProperties": {"private": {DONE_KEY: "1" if done else "0"}}}


def _clean(body):
    """insert용: None 칸 빼기"""
    if isinstance(body, dict):
        return {k: _clean(v) for k, v in body.items() if v is not None}
    return body


class GoogleStore:
    kind = "google"

    def __init__(self, app_dir, client_secret_path, tz_name):
        self.app_dir = app_dir
        self.client_secret = client_secret_path
        self.tz_name = tz_name
        self.tz = core.get_tz(tz_name)
        self.token_path = os.path.join(app_dir, "token.json")
        self.svc = None
        self.tsvc = None          # 구글 할 일 (할 일 권한을 받았을 때만)
        self.can_set_remind = False   # 구글 캘린더 기본 알림을 바꿀 권한
        self.default_remind = core.load_json(os.path.join(app_dir, "google.json"), {}).get("default_remind")   # 구글 기본 알림(분, -1=없음)
        self.email = core.load_json(os.path.join(app_dir, "google.json"), {}).get("email", "")

    def label(self):
        return self.email or "구글 캘린더"

    def has_token(self):
        return os.path.exists(self.token_path)

    def connect(self, interactive=False):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(self.token_path):
            try:
                creds = Credentials.from_authorized_user_file(self.token_path)   # 받은 권한 그대로 (할 일 권한이 없는 옛 토큰도)
            except Exception:
                creds = None
        if creds and not creds.valid and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                if not interactive:
                    raise NeedLogin("구글 로그인이 만료됐어요. ⋯ → 설정에서 구글 캘린더를 다시 연결해 주세요.")
                creds = None
        if interactive and creds and not (_has_tasks(creds) and _has_scope(creds, CALLIST_SCOPE)):
            creds = None   # 할 일·기본 알림 권한이 없는 옛 연결이면 다시 로그인해서 권한을 더 받는다
        if not creds or not creds.valid:
            if not interactive:
                raise NeedLogin("구글 캘린더가 연결되어 있지 않아요.")
            if not os.path.exists(self.client_secret):
                raise RuntimeError("credentials.json이 없어요. 앱 폴더에 넣어 주세요 (README 참고).")
            flow = InstalledAppFlow.from_client_secrets_file(self.client_secret, SCOPES)
            creds = flow.run_local_server(
                port=0, open_browser=True, timeout_seconds=300,
                success_message="구글 캘린더 연결 완료! 이 창은 닫아도 돼요.")
            if not creds:
                raise RuntimeError("구글 로그인을 끝내지 못했어요 (5분 안에 로그인해 주세요).")
        os.makedirs(self.app_dir, exist_ok=True)
        with open(self.token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        self.svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.tsvc = build("tasks", "v1", credentials=creds, cache_discovery=False) if _has_tasks(creds) else None
        self.can_set_remind = _has_scope(creds, CALLIST_SCOPE)

    def _api(self):
        if self.svc is None:
            self.connect(interactive=False)
        return self.svc.events()

    def logout(self):
        self.svc = self.tsvc = None
        for p in (self.token_path, os.path.join(self.app_dir, "google.json")):
            if os.path.exists(p):
                os.remove(p)

    def list_range(self, f, t):
        items, token = [], None
        while True:
            res = self._api().list(
                calendarId="primary", timeMin=_rfc(datetime.datetime.combine(f, datetime.time(0))),
                timeMax=_rfc(datetime.datetime.combine(t + datetime.timedelta(days=1), datetime.time(0))),
                singleEvents=True, orderBy="startTime", maxResults=250, pageToken=token).execute()
            items += res.get("items", [])
            dr = popup_min(res.get("defaultReminders"))   # 구글 캘린더 기본 알림 (캘린더 권한만으로도 읽힌다)
            if (res.get("summary") and res["summary"] != self.email) or dr != self.default_remind:   # 기본 캘린더 이름 = 계정 메일
                self.email, self.default_remind = res.get("summary") or self.email, dr
                core.save_json(os.path.join(self.app_dir, "google.json"), {"email": self.email, "default_remind": dr})
            token = res.get("nextPageToken")
            if not token or len(items) > 2000:
                break
        return sorted((from_google(e, self.tz) for e in items if e.get("status") != "cancelled"), key=_sort_key)

    def set_default_remind(self, minutes):
        """구글 캘린더 기본 알림을 바꾼다 (휴대폰·구글 캘린더 앱도 같이). minutes: 분, -1 = 알림 없음. 메일 알림은 그대로"""
        if self.svc is None:
            self.connect(interactive=False)
        if not self.can_set_remind:
            raise NeedCalListScope("기본 알림을 바꾸려면 구글 권한을 하나 더 받아야 해요. 구글을 다시 연결해 주세요.")
        cl = self.svc.calendarList()
        entry = cl.get(calendarId="primary").execute()   # 읽기는 'primary'로 되지만 바꾸기는 진짜 id(계정 메일)여야 한다
        keep = [r for r in entry.get("defaultReminders") or [] if r.get("method") != "popup"]
        cl.patch(calendarId=entry["id"], body={"defaultReminders": keep + ([] if minutes < 0 else [{"method": "popup", "minutes": int(minutes)}])}).execute()
        self.default_remind = int(minutes)
        core.save_json(os.path.join(self.app_dir, "google.json"), {"email": self.email, "default_remind": self.default_remind})

    def _get(self, eid):
        return self._api().get(calendarId="primary", eventId=eid).execute()

    def _day(self, x):
        """구글 start/end 칸 → 이 지역 날짜"""
        if x.get("date"):
            return _d(x["date"])
        return datetime.datetime.fromisoformat(x["dateTime"].replace("Z", "+00:00")).astimezone(self.tz).date()

    def get(self, eid):
        ev = self._get(eid)
        u = from_google(ev, self.tz)
        master = self._get(ev["recurringEventId"]) if ev.get("recurringEventId") else ev
        rule = next((r for r in master.get("recurrence", []) if r.upper().startswith("RRULE:")), None)
        u["rrule"] = core.norm_rrule(rule)
        ms = master.get("start", {})
        md = (ms.get("dateTime") or ms.get("date") or u["date"])[:10]
        u["rrule_text"] = core.describe_rrule(u["rrule"], _d(md))
        return u

    def add(self, spec):
        created = self._api().insert(calendarId="primary", body=to_google(spec, self.tz_name)).execute()
        return created["id"], [{"t": "delete", "id": created["id"]}]

    def update(self, eid, spec, scope):
        ev = self._get(eid)
        mid = ev.get("recurringEventId")
        if mid and scope == "all":
            master = self._get(mid)
            if not spec.get("rrule"):         # 반복 끄기: 반복 전체를 지우고 단일 일정 하나로
                new_id, rec = self.add(spec)
                self._api().delete(calendarId="primary", eventId=mid).execute()
                return rec + [{"t": "undelete", "id": mid, "body": _snapshot(master)}]
            # 고른 회차에서 옮긴 만큼 반복 시작일도 옮긴다
            inst_day = self._day(ev.get("originalStartTime") or ev["start"])
            m_day = self._day(master["start"])
            shift = _d(spec["date"]) - inst_day
            span = _d(spec["end_date"]) - _d(spec["date"])
            mspec = dict(spec, date=(m_day + shift).isoformat(), end_date=(m_day + shift + span).isoformat())
            self._api().patch(calendarId="primary", eventId=mid, body=to_google(mspec, self.tz_name, patch=True)).execute()
            return [{"t": "patch", "id": mid, "body": _snapshot(master)}]
        if mid:   # 반복의 한 회차만
            body = to_google(spec, self.tz_name, patch=True, recurrence=False)
            self._api().patch(calendarId="primary", eventId=eid, body=body).execute()
            return [{"t": "patch", "id": eid, "body": _snapshot(ev, recurrence=False)}]
        body = to_google(spec, self.tz_name, patch=True)
        if not spec.get("rrule") and ev.get("recurrence"):
            body["recurrence"] = None
        self._api().patch(calendarId="primary", eventId=eid, body=body).execute()
        snap = _snapshot(ev)
        snap.setdefault("recurrence", None)
        return [{"t": "patch", "id": eid, "body": snap}]

    def delete(self, eid, scope):
        ev = self._get(eid)
        mid = ev.get("recurringEventId")
        target, snap = (mid, _snapshot(self._get(mid))) if (mid and scope == "all") else (eid, _snapshot(ev, not mid))
        self._api().delete(calendarId="primary", eventId=target).execute()
        return [{"t": "undelete", "id": target, "body": snap}]

    def set_done(self, eid, done):
        """완료 체크. 반복 일정의 회차 id면 그 회차만 바뀐다"""
        self._api().patch(calendarId="primary", eventId=eid, body=_done_body(done)).execute()
        return [{"t": "patch", "id": eid, "body": _done_body(not done)}]

    def undo(self, records):
        from googleapiclient.errors import HttpError
        api = self._api()
        for r in reversed(records):
            if r["t"] == "delete":
                try:
                    api.delete(calendarId="primary", eventId=r["id"]).execute()
                except HttpError as e:
                    if e.resp.status not in (404, 410):
                        raise
            elif r["t"] == "patch":
                api.patch(calendarId="primary", eventId=r["id"], body=r["body"]).execute()
            elif r["t"] == "undelete":
                try:   # 지운 일정은 '취소됨' 상태로 남아 있어서 되살릴 수 있다
                    api.patch(calendarId="primary", eventId=r["id"], body={"status": "confirmed", **r["body"]}).execute()
                except HttpError:
                    api.insert(calendarId="primary", body=_clean(r["body"])).execute()
            elif r["t"] == "task_delete":
                try:
                    self._task_call(self._tasks().delete(tasklist="@default", task=r["id"]))
                except KeyError:
                    pass
            elif r["t"] == "task_patch":
                self._task_call(self._tasks().patch(tasklist="@default", task=r["id"], body=r["body"]))
            elif r["t"] == "task_insert":
                self._task_call(self._tasks().insert(tasklist="@default", body=_clean(r["body"])))

    # ----- 기약 없는 할 일 (구글 할 일의 기본 목록) -----
    def _tasks(self):
        if self.svc is None:
            self.connect(interactive=False)
        if self.tsvc is None:
            raise NeedTasksScope(TASKS_SCOPE_MSG)
        return self.tsvc.tasks()

    @staticmethod
    def _task_call(req):
        from googleapiclient.errors import HttpError
        try:
            return req.execute()
        except HttpError as e:
            text = str(e)
            if e.resp.status == 403 and ("insufficient" in text.lower() or "scope" in text.lower()):
                raise NeedTasksScope(TASKS_SCOPE_MSG) from e
            if e.resp.status == 403 and ("has not been used" in text or "disabled" in text.lower()):
                raise RuntimeError("구글 클라우드에서 Tasks API가 꺼져 있어요. 배포한 사람이 켜야 해요 (README 참고).") from e
            if e.resp.status in (404, 410):
                raise KeyError("할 일을 찾지 못했어요 (이미 지워졌을 수 있어요).") from e
            raise

    def list_tasks(self):
        items, token = [], None
        while True:
            res = self._task_call(self._tasks().list(tasklist="@default", showCompleted=True, showHidden=False,
                                                     maxResults=100, pageToken=token))
            items += res.get("items", [])
            token = res.get("nextPageToken")
            if not token or len(items) > 1000:
                break
        return _task_order([task_from_google(t) for t in items if not t.get("deleted") and (t.get("title") or "").strip()])

    def get_task(self, tid):
        return task_from_google(self._task_call(self._tasks().get(tasklist="@default", task=tid)))

    def add_task(self, title, notes="", state="todo"):
        body = {"title": title}
        if notes:
            body["notes"] = notes
        if state == "done":
            body["status"] = "completed"
        t = self._task_call(self._tasks().insert(tasklist="@default", body=body))   # 맨 위에 들어간다
        return [{"t": "task_delete", "id": t["id"]}]

    def set_task(self, tid, state):
        old = self._task_call(self._tasks().get(tasklist="@default", task=tid))
        base = task_from_google(old)["title"]
        body = {"title": base, "status": "completed" if state == "done" else "needsAction"}
        if state != "done":
            body["completed"] = None
        self._task_call(self._tasks().patch(tasklist="@default", task=tid, body=body))
        back = {"title": old.get("title", ""), "status": old.get("status", "needsAction")}
        if back["status"] != "completed":
            back["completed"] = None
        return [{"t": "task_patch", "id": tid, "body": back}]

    def edit_task(self, tid, title, notes):
        """제목·메모 고치기"""
        old = self._task_call(self._tasks().get(tasklist="@default", task=tid))
        body = {"title": title, "notes": notes or None}
        self._task_call(self._tasks().patch(tasklist="@default", task=tid, body=body))
        return [{"t": "task_patch", "id": tid, "body": {"title": old.get("title", ""), "notes": old.get("notes")}}]

    def delete_task(self, tid):
        old = self._task_call(self._tasks().get(tasklist="@default", task=tid))
        self._task_call(self._tasks().delete(tasklist="@default", task=tid))
        return [{"t": "task_insert", "body": {k: old.get(k) for k in ("title", "notes", "status", "due")}}]

    def clear_done(self):
        recs = []
        for t in self.list_tasks():
            if t["state"] == "done":
                recs += self.delete_task(t["id"])
        return recs

    def import_tasks(self, tasks):
        """PC에 저장했던 할 일을 구글로 옮기기 (오래된 것부터 넣어서 최근 것이 위로). 옮긴 개수"""
        n = 0
        for t in sorted(tasks, key=lambda t: t.get("created", "")):
            body = {"title": t["title"]}
            if t.get("notes"):
                body["notes"] = t["notes"]
            if t.get("state") == "done":
                body["status"] = "completed"
            self._task_call(self._tasks().insert(tasklist="@default", body=body))
            n += 1
        return n

    def import_local(self, masters):
        """PC에 저장했던 일정을 구글로 옮기기. 옮긴 개수"""
        n = 0
        for m in masters:
            spec = core.normalize(m)
            if not spec:
                continue
            body = to_google(spec, self.tz_name)
            ex = m.get("exdates") or []
            if spec.get("rrule") and ex:
                if spec.get("time"):
                    hhmmss = spec["time"].replace(":", "") + "00"
                    body["recurrence"] += [f"EXDATE;TZID={self.tz_name}:" + ",".join(x.replace("-", "") + "T" + hhmmss for x in ex)]
                else:
                    body["recurrence"] += ["EXDATE;VALUE=DATE:" + ",".join(x.replace("-", "") for x in ex)]
            if m.get("done") and not spec.get("rrule"):
                body.update(_done_body(True))
            self._api().insert(calendarId="primary", body=body).execute()
            n += 1
        return n
