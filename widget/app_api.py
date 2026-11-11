# -*- coding: utf-8 -*-
"""화면(ui/index.html)에서 부르는 기능 — pywebview js_api.

화면 → 파이썬: window.pywebview.api.이름(...)  (각 호출은 따로 스레드에서 돈다)
파이썬 → 화면: window.__emit(종류, 내용)        (창 스레드가 아닌 곳에서만 부를 것)
"""
import os
import sys
import glob
import json
import time
import shutil
import ctypes
import secrets
import datetime
import threading
import subprocess
import webbrowser

import calcore as core
import stores
import updater

IS_WIN = core.IS_WIN
if IS_WIN:
    import ctypes.wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = core.APP_DIR
CLIENT_SECRET = os.path.join(HERE, "credentials.json")
WINDOW_TITLE = "ClaudeCal"
OPEN_EVENT = "ClaudeCalOpenRequest"
SIZES = {"button": (56, 56), "small": (380, 600), "big": (1040, 720)}
TASK_W = 250   # 왼쪽 할 일 칸을 켜면 창이 이만큼 왼쪽으로 넓어진다


# ---------------- 전역 단축키 ----------------
VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "`": 0xC0, **{f"f{n}": 0x6F + n for n in range(1, 13)}}
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}


def parse_hotkey(s):
    mods, vk = 0, None
    for p in s.lower().replace(" ", "").split("+"):
        if p in MODS:
            mods |= MODS[p]
        elif p in VK:
            vk = VK[p]
        elif len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
    return mods, vk


def work_area():
    """작업 표시줄을 뺀 주 모니터 영역 (왼, 위, 오른, 아래) — pywebview와 같은 논리 픽셀(배율 나눔)"""
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    s = ctypes.windll.user32.GetDpiForSystem() / 96
    return int(r.left / s), int(r.top / s), int(r.right / s), int(r.bottom / s)


def virtual_screen():
    """모든 모니터를 합친 영역 (x, y, 너비, 높이) — 논리 픽셀"""
    m = ctypes.windll.user32.GetSystemMetrics
    s = ctypes.windll.user32.GetDpiForSystem() / 96
    return tuple(int(m(i) / s) for i in (76, 77, 78, 79))


def downloads_dir():
    """다운로드 폴더 (원드라이브 등으로 옮겨 둔 경우까지)"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0])
    except OSError:
        return os.path.join(os.path.expanduser("~"), "Downloads")


def find_client_secret(copy=False):
    """구글 열쇠 파일: 앱 폴더의 credentials.json. 없으면 앱 폴더·다운로드 폴더에서 구글 콘솔이 내려 준
    client_secret_….json(데스크톱 앱용)을 찾아 copy=True면 credentials.json으로 가져온다."""
    if os.path.exists(CLIENT_SECRET):
        return CLIENT_SECRET
    found = sorted(glob.glob(os.path.join(HERE, "client_secret*.json")) +
                   glob.glob(os.path.join(downloads_dir(), "client_secret*.json")), key=os.path.getmtime, reverse=True)
    for p in found:
        data = core.load_json(p, {})
        if "installed" in data:
            if copy:
                shutil.copyfile(p, CLIENT_SECRET)
            return p
    if copy and found:
        raise RuntimeError("다운로드한 열쇠 파일이 '데스크톱 앱'용이 아니에요. 클라이언트를 만들 때 유형을 '데스크톱 앱'으로 골라 주세요.")
    return None


def startup_link():
    return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\ClaudeCal.lnk")


def set_autostart(on):
    link = startup_link()
    if not on:
        if os.path.exists(link):
            os.remove(link)
        return
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    script = os.path.join(HERE, "claude_cal.pyw")
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath=$env:EXE; "
          "$s.Arguments='\"' + $env:SCRIPT + '\" --button'; $s.WorkingDirectory=$env:DIR; $s.Save()")   # 켤 때는 버튼으로만
    env = dict(os.environ, LNK=link, EXE=exe, SCRIPT=script, DIR=HERE)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True,
                   creationflags=0x08000000, timeout=30)


class Api:
    def __init__(self):
        os.makedirs(APP_DIR, exist_ok=True)
        self._window = None
        self._ui_ready = False
        self._revealed = False   # 창을 처음 보여 줬는지 (reveal)
        self._really_quit = False
        self._mode = "small"     # button(오른쪽 아래 작은 버튼) | small(목록) | big(주·월)
        self._save_timer = None
        self._lock = threading.RLock()
        self._conf = core.load_conf()
        core.set_tz(self._conf["timezone"])
        self._undo = []          # [{"desc", "kind", "batch"}] 최근 것이 끝
        self._pending = {}       # 확인 기다리는 할 일: token → plan
        self._fired = set()      # 이미 알린 일정
        self._soon = []          # 알림용 가까운 일정
        self._soon_at = 0
        self._update_ready = None   # 받아 둔 새 버전 (다음에 버튼으로 접혀 있을 때 다시 켜서 적용)
        self._update_lock = threading.Lock()
        self._just_updated = core.load_json(os.path.join(APP_DIR, "updated.json"), None)   # 방금 업데이트하고 다시 켜졌으면
        if self._just_updated:
            try:
                os.remove(os.path.join(APP_DIR, "updated.json"))
            except OSError:
                pass
        self._open_store()

    # ----- 저장소 -----
    def _open_store(self):
        g = stores.GoogleStore(APP_DIR, CLIENT_SECRET, self._conf["timezone"])
        self._store = g if g.has_token() else stores.LocalStore(os.path.join(APP_DIR, "events.json"))
        self._undo.clear()
        self._soon_at = 0

    def _emit(self, kind, data=None):
        if self._window and self._ui_ready:
            try:
                self._window.evaluate_js(f"window.__emit({json.dumps(kind)}, {json.dumps(data, ensure_ascii=False)})")
            except Exception:
                pass

    def _emit_later(self, kind, data=None):
        threading.Thread(target=self._emit, args=(kind, data), daemon=True).start()

    @staticmethod
    def _ok(**kw):
        return {"ok": True, **kw}

    @staticmethod
    def _err(e):
        if isinstance(e, stores.NeedTasksScope):
            return {"ok": False, "error": str(e), "need_tasks_scope": True}
        if isinstance(e, stores.NeedCalListScope):
            return {"ok": False, "error": str(e), "need_callist_scope": True}
        msg = str(e) if isinstance(e, (RuntimeError, KeyError, stores.NeedLogin, ValueError)) else f"{type(e).__name__}: {e}"
        return {"ok": False, "error": msg.strip("'\"")[:300]}

    def _remind_default(self):
        """'기본' 알림(분, -1=없음): 구글 연결이면 구글 캘린더 기본 알림 (휴대폰·구글 앱과 같은 값), 아니면 이 PC 설정"""
        g = getattr(self._store, "default_remind", None)
        return g if g is not None else int(self._conf.get("remind_min", 10))

    def _state(self):
        c = {**self._conf, "remind_min": self._remind_default()}
        key = c.get("api_key", "")
        return {
            "store": self._store.kind, "store_label": self._store.label(),
            "local_count": self._store.count() if self._store.kind == "local" else 0,
            "has_credentials": bool(find_client_secret()),
            "engine": core.engine_name(c), "has_cli": bool(core.find_claude()),
            "api_key_hint": (key[:7] + "…" + key[-4:]) if len(key) > 12 else ("설정됨" if key else ""),
            "conf": {**{k: c[k] for k in ("hotkey", "engine", "model", "quick_parse", "confirm_delete", "notify",
                                           "remind_min", "on_top", "days_ahead", "view", "tasks_panel", "show_done",
                                           "auto_update")},
                     "autostart": os.path.exists(startup_link())},   # 실제 시작 프로그램 바로가기가 있는지
            "version": updater.local_version(HERE), "dev": updater.is_dev(HERE),
            "update_ready": (self._update_ready or {}).get("version", ""), "just_updated": self._just_updated,
            "can_undo": bool(self._undo), "undo_desc": self._undo[-1]["desc"] if self._undo else "",
            "today": core.now_local().date().isoformat(), "mode": self._mode,
            "remind_google": self._store.kind == "google", "remind_can_set": bool(getattr(self._store, "can_set_remind", False)),
        }

    # ----- 화면 시작 -----
    def init(self):
        self._ui_ready = True
        st = self._state()
        self._just_updated = None   # '업데이트했어요' 안내는 처음 한 번만
        return st

    def state(self):
        return self._state()

    def events(self, f, t):
        try:
            with self._lock:
                evs = self._store.list_range(core.valid_date(f), core.valid_date(t))
            return self._ok(events=evs, store_label=self._store.label())
        except Exception as e:
            return self._err(e)

    def event(self, eid):
        try:
            with self._lock:
                return self._ok(event=self._store.get(eid))
        except Exception as e:
            return self._err(e)

    # ----- 한 줄 입력 -----
    def submit(self, text):
        text = (text or "").strip()
        if not text:
            return self._err(RuntimeError("내용을 적어 주세요."))
        t0 = time.time()
        try:
            now = core.now_local()
            quick = core.quick_parse(text, now.date(), now.hour * 60 + now.minute) if self._conf.get("quick_parse", True) else None
            if quick:
                data, sid_map = {"actions": [quick], "reply": ""}, {}
            else:
                with self._lock:
                    near = self._store.list_range(now.date() - datetime.timedelta(days=7),
                                                  now.date() + datetime.timedelta(days=90))[:200]
                brief, sid_map = core.brief_events(near)
                data = core.ask_claude(self._conf, text, brief, now, APP_DIR)
            with self._lock:
                plan, query = core.make_plan(data, sid_map, self._store)
            reply = data["reply"]
            res = {"ok": True, "quick": bool(quick), "query": query, "secs": round(time.time() - t0, 1)}
            if not plan:
                res["reply"] = reply or ("" if query else "할 일을 찾지 못했어요. 날짜와 할 일을 같이 적어 주세요.")
                return res
            if self._conf.get("confirm_delete", True) and any(p["op"] == "delete" or p.get("scope") == "all" for p in plan):
                token = secrets.token_hex(6)
                self._pending = {token: {"plan": plan, "reply": reply, "text": text}}
                res.update(pending={"token": token, "items": [p["desc"] for p in plan]}, reply=reply)
                return res
            res.update(self._run(plan, reply, text))
            return res
        except Exception as e:
            return self._err(e)

    def _run(self, plan, reply, text):
        with self._lock:
            batch = core.execute_plan(self._store, plan)
            self._undo.append({"desc": text, "kind": self._store.kind, "batch": batch})
            del self._undo[:-30]
            self._soon_at = 0
        if not reply:
            items, ops = " / ".join(p["desc"].split(" · ", 1)[-1] for p in plan), {p["op"] for p in plan}
            reply = ("할 일에 넣었어요: " + items) if ops == {"task"} else items + (" 등록했어요" if ops == {"add"} else " 처리했어요")
        return {"reply": reply, "done": [p["desc"] for p in plan], "can_undo": True}

    def confirm(self, token, yes):
        job = self._pending.pop(token, None)
        if not job:
            return self._err(RuntimeError("이미 처리했거나 취소한 요청이에요."))
        if not yes:
            return self._ok(reply="취소했어요. 아무것도 바꾸지 않았어요.")
        try:
            return self._ok(**self._run(job["plan"], job["reply"], job["text"]))
        except Exception as e:
            return self._err(e)

    def undo(self):
        with self._lock:
            if not self._undo:
                return self._err(RuntimeError("되돌릴 게 없어요."))
            item = self._undo.pop()
            if item["kind"] != self._store.kind:
                return self._err(RuntimeError("저장 위치가 바뀌어 되돌릴 수 없어요."))
            try:
                core.undo_batch(self._store, item["batch"])
            except Exception as e:
                return self._err(e)
            self._soon_at = 0
        return self._ok(reply=f"되돌렸어요: “{item['desc'][:30]}”", can_undo=bool(self._undo))

    # ----- 상세 창에서 직접 고치기 -----
    def save(self, spec, eid=None, scope="one"):
        spec = core.normalize(spec or {})
        if not spec:
            return self._err(RuntimeError("제목과 날짜를 넣어 주세요."))
        if eid:
            plan = [{"op": "update", "id": eid, "scope": scope, "spec": spec, "desc": "변경 · " + core.describe(spec)}]
        else:
            plan = [{"op": "add", "spec": spec, "desc": "추가 · " + core.describe(spec)}]
        try:
            r = self._run(plan, "", ("수정: " if eid else "추가: ") + spec["title"])
            r["reply"] = ("저장했어요: " if eid else "등록했어요: ") + core.describe(spec)
            return self._ok(**r)
        except Exception as e:
            return self._err(e)

    def remove(self, eid, scope="one", title=""):
        try:
            r = self._run([{"op": "delete", "id": eid, "scope": scope, "desc": "삭제 · " + title}], "", "삭제: " + title)
            r["reply"] = f"삭제했어요: {title}" + (" (반복 전체)" if scope == "all" else "")
            return self._ok(**r)
        except Exception as e:
            return self._err(e)

    def set_done(self, eid, done, title=""):
        """완료 체크 켜기/끄기 (되돌리기 가능)"""
        try:
            with self._lock:
                rec = self._store.set_done(eid, bool(done))
                self._undo.append({"desc": ("완료: " if done else "완료 취소: ") + title, "kind": self._store.kind, "batch": [rec]})
                del self._undo[:-30]
                self._soon_at = 0
            return self._ok(can_undo=True)
        except Exception as e:
            return self._err(e)

    # ----- 설정 -----
    def set_settings(self, changes):
        allowed = {"engine", "model", "quick_parse", "confirm_delete", "notify", "remind_min", "on_top",
                   "days_ahead", "view", "hotkey", "autostart", "tasks_panel", "show_done", "auto_update"}
        changes = {k: v for k, v in (changes or {}).items() if k in allowed}
        if "remind_min" in changes and self._store.kind == "google":   # 구글 캘린더 기본 알림을 바꾼다 → 휴대폰·구글 앱도 같이
            try:
                with self._lock:
                    self._store.set_default_remind(int(changes["remind_min"]))
                self._soon_at = 0
            except Exception as e:
                return {**self._err(e), "state": self._state()}
        self._conf.update(changes)
        core.save_json(core.CONF_FILE, self._conf)
        if "on_top" in changes and self._window:
            self._window.on_top = bool(changes["on_top"])
        if "tasks_panel" in changes and self._mode != "button":
            self._place(self._mode)   # 할 일 칸만큼 창을 넓히거나 줄인다
        if "autostart" in changes and IS_WIN:
            try:
                set_autostart(bool(changes["autostart"]))
            except Exception as e:
                return self._err(e)
        return self._ok(state=self._state())

    def set_api_key(self, key):
        key = (key or "").strip()
        if key and not key.startswith("sk-ant-"):
            return self._err(RuntimeError("Anthropic API 키는 sk-ant- 로 시작해요. 다시 확인해 주세요."))
        if key:
            problem = core.test_api_key(dict(self._conf, api_key=key))
            if problem:
                return self._err(RuntimeError(problem))
        self._conf["api_key"] = key
        core.save_json(core.CONF_FILE, self._conf)
        return self._ok(state=self._state(), reply="API 키를 저장했어요." if key else "API 키를 지웠어요.")

    def test_claude(self):
        eng = core.engine_name(self._conf)
        if eng == "api":
            problem = core.test_api_key(self._conf)
            return self._err(RuntimeError(problem)) if problem else self._ok(reply="API 키 정상이에요.")
        if eng == "cli":
            return self._ok(reply="Claude Code로 연결돼요 (본인 구독 사용량에서 차감).")
        return self._err(RuntimeError("Claude 연결이 없어요."))

    # ----- 구글 -----
    def google_connect(self):
        try:
            if not find_client_secret(copy=True):
                return {"ok": False, "need_setup": True, "error": "구글 열쇠 파일(credentials.json)이 아직 없어요."}
        except Exception as e:
            return self._err(e)
        try:
            g = stores.GoogleStore(APP_DIR, CLIENT_SECRET, self._conf["timezone"])
            g.connect(interactive=True)
            today = core.now_local().date()
            g.list_range(today, today)   # 계정 메일 알아 두기
        except Exception as e:
            return self._err(e)
        local = self._store if self._store.kind == "local" else None
        with self._lock:
            self._store = g
            self._undo.clear()
            self._soon_at = 0
        return self._ok(state=self._state(), local_count=(local.count() + len(local.raw_tasks())) if local else 0)

    def google_disconnect(self):
        with self._lock:
            if self._store.kind == "google":
                self._store.logout()
            self._open_store()
        return self._ok(state=self._state())

    def migrate_local(self):
        """PC에 저장해 둔 일정을 구글로 옮기고, PC 쪽은 백업 파일로 남긴 뒤 비운다"""
        if self._store.kind != "google":
            return self._err(RuntimeError("구글 캘린더가 연결되어 있지 않아요."))
        local = stores.LocalStore(os.path.join(APP_DIR, "events.json"))
        try:
            with self._lock:   # 할 일 먼저 (권한이 없으면 아무것도 옮기기 전에 멈추게)
                nt = self._store.import_tasks(local.raw_tasks()) if local.raw_tasks() else 0
                n = self._store.import_local(local.masters())
            local.clear(os.path.join(APP_DIR, f"events_옮김_{datetime.datetime.now():%Y%m%d_%H%M%S}.json"))
            return self._ok(reply=f"PC 일정 {n}개" + (f", 할 일 {nt}개" if nt else "") + "를 구글로 옮겼어요.", state=self._state())
        except Exception as e:
            return self._err(e)

    # ----- 기약 없는 할 일 -----
    def tasks(self):
        try:
            with self._lock:
                return self._ok(tasks=self._store.list_tasks())
        except Exception as e:
            return self._err(e)

    def _task_run(self, desc, fn, *args):
        """할 일 바꾸기 → 되돌리기 기록 쌓고 새 목록 돌려주기"""
        try:
            with self._lock:
                rec = fn(*args)
                if rec:
                    self._undo.append({"desc": desc, "kind": self._store.kind, "batch": [rec]})
                    del self._undo[:-30]
                return self._ok(can_undo=bool(self._undo), tasks=self._store.list_tasks())
        except Exception as e:
            return self._err(e)

    def task_add(self, title):
        title = " ".join((title or "").split())[:300]
        if not title:
            return self._err(RuntimeError("할 일을 적어 주세요."))
        return self._task_run("할 일: " + title, self._store.add_task, title)

    def task_set(self, tid, state, title=""):
        if state not in stores.TASK_STATES:
            return self._err(ValueError("알 수 없는 상태예요."))
        word = {"todo": "할 일로", "done": "완료"}[state]
        return self._task_run(f"{word}: {title}", self._store.set_task, tid, state)

    def task_edit(self, tid, title, notes):
        title = " ".join((title or "").split())[:300]
        if not title:
            return self._err(RuntimeError("할 일 이름을 비울 수는 없어요."))
        return self._task_run("할 일 수정: " + title, self._store.edit_task, tid, title, (notes or "").strip()[:8000])

    def task_delete(self, tid, title=""):
        return self._task_run("할 일 삭제: " + title, self._store.delete_task, tid)

    def task_clear_done(self):
        return self._task_run("완료한 할 일 지우기", self._store.clear_done)

    # ----- 끌어서 옮기기 (일정 날짜 바꾸기 · 할 일 ↔ 일정) -----
    def _push_undo(self, desc, batch):
        self._undo.append({"desc": desc, "kind": self._store.kind, "batch": batch})
        del self._undo[:-30]
        self._soon_at = 0

    def _do_all(self, steps):
        """steps를 차례로 실행해 되돌리기 기록 묶음을 만든다. 중간에 실패하면 앞의 것까지 되돌린다"""
        batch = []
        try:
            for fn in steps:
                batch.append(fn())
        except Exception:
            core.undo_batch(self._store, batch)
            raise
        return batch

    def move_event(self, eid, days):
        """일정을 days일만큼 옮긴다. 시간·길이는 그대로, 반복 일정은 그 회차만"""
        try:
            days = int(days)
            with self._lock:
                ev = self._store.get(eid)
                if not days:
                    return self._ok(reply="", can_undo=bool(self._undo))
                sh = lambda s: (core.valid_date(s) + datetime.timedelta(days=days)).isoformat()
                spec = core.normalize({**ev, "date": sh(ev["date"]), "end_date": sh(ev["end_date"]),
                                       "rrule": None if ev.get("recurring") else ev.get("rrule")})
                batch = self._do_all([lambda: self._store.update(eid, spec, "one")])
                self._push_undo("옮기기: " + spec["title"], batch)
            return self._ok(reply="옮겼어요: " + core.describe(spec) + (" (반복 중 이 회차만)" if ev.get("recurring") else ""),
                            can_undo=True)
        except Exception as e:
            return self._err(e)

    def task_to_event(self, tid, date, time=None):
        """할 일 → 그 날짜의 일정 (시간이 없으면 종일, 메모는 일정 메모로). 할 일 목록에서는 빠진다"""
        try:
            with self._lock:
                t = self._store.get_task(tid)
                spec = core.normalize({"title": t["title"], "date": date, "time": time, "note": t.get("notes") or ""})
                if not spec:
                    raise ValueError("날짜를 알아보지 못했어요.")
                new = {}

                def add():
                    new["id"], rec = self._store.add(spec)
                    return rec
                steps = [add]
                if t["state"] == "done":
                    steps.append(lambda: self._store.set_done(new["id"], True))
                steps.append(lambda: self._store.delete_task(tid))
                batch = self._do_all(steps)
                self._push_undo("일정으로: " + t["title"], batch)
                tasks = self._store.list_tasks()
            return self._ok(reply="일정으로 옮겼어요: " + core.describe(spec), can_undo=True, tasks=tasks)
        except Exception as e:
            return self._err(e)

    def event_to_task(self, eid):
        """일정 → 날짜 없는 할 일 (장소·메모는 할 일 메모로, 완료 체크도 그대로). 일정은 지운다"""
        try:
            with self._lock:
                ev = self._store.get(eid)
                if ev.get("recurring"):
                    raise RuntimeError("반복 일정은 할 일로 옮길 수 없어요. 한 번뿐인 일정만 돼요.")
                notes = "\n".join(x for x in (ev.get("location") and "📍 " + ev["location"], ev.get("note")) if x)
                batch = self._do_all([lambda: self._store.add_task(ev["title"], notes, "done" if ev.get("done") else "todo"),
                                      lambda: self._store.delete(eid, "one")])
                self._push_undo("할 일로: " + ev["title"], batch)
                tasks = self._store.list_tasks()
            return self._ok(reply="할 일로 옮겼어요: " + ev["title"], can_undo=True, tasks=tasks)
        except Exception as e:
            return self._err(e)

    # ----- 창 -----
    def open_link(self, url):
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)

    def open_folder(self, which="app"):
        if IS_WIN:
            os.startfile(HERE if which == "app" else APP_DIR)

    # 창은 하나로: 오른쪽 아래 작은 버튼(button) ↔ 목록 창(small) ↔ 주·월 창(big)
    @staticmethod
    def _hwnd():
        return ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE) if IS_WIN else 0

    def _rect(self):
        """지금 창 (왼, 위, 오른, 아래) — 논리 픽셀"""
        h = self._hwnd()
        if not h:
            return None
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r))
        s = ctypes.windll.user32.GetDpiForWindow(h) / 96
        return r.left / s, r.top / s, r.right / s, r.bottom / s

    def _button_pos(self):
        bw, bh = SIZES["button"]
        vx, vy, vw, vh = virtual_screen()
        x, y = self._conf.get("bx"), self._conf.get("by")
        if x is None or y is None or not (vx <= x <= vx + vw - bw and vy <= y <= vy + vh - bh):
            l, t, r, b = work_area()
            x, y = r - bw - 14, b - bh - 14
        return x, y

    def geometry(self, mode):
        """mode일 때 창 (x, y, 너비, 높이). 버튼은 제자리, 창은 버튼(창끼리 바꿀 땐 지금 창)의
        오른쪽 아래 모서리에 맞춰 위로 펼치고 화면 안으로 당긴다."""
        width, height = SIZES[mode]
        if mode == "button":
            return (*self._button_pos(), width, height)
        if self._conf.get("tasks_panel", True):
            width += TASK_W
        cur = self._rect() if self._mode in ("small", "big") else None
        if cur:
            right, bottom = cur[2], cur[3]
        else:
            bx, by = self._button_pos()
            right, bottom = bx + SIZES["button"][0], by + SIZES["button"][1]
        l, t, r, b = work_area()
        width, height = min(width, r - l - 16), min(height, b - t - 16)   # 작은 화면에서도 넘치지 않게
        x = max(l + 8, min(right - width, r - width - 8))
        y = max(t + 8, min(bottom - height, b - height - 8))
        return x, y, width, height

    def _place(self, mode):
        x, y, width, height = self.geometry(mode)
        self._mode = mode
        h = self._hwnd()
        if h:   # 옮기기와 크기 바꾸기를 한 번에 (깜빡임 없게)
            s = ctypes.windll.user32.GetDpiForWindow(h) / 96
            ctypes.windll.user32.SetWindowPos(h, 0, int(x * s), int(y * s), int(width * s), int(height * s), 0x0004 | 0x0010)

    def set_mode(self, mode):
        if mode not in SIZES:
            return
        self._place(mode)
        if mode != "button":
            self.focus_window()

    def focus_window(self):
        h = self._hwnd()
        if h:
            ctypes.windll.user32.ShowWindow(h, 9)   # 최소화돼 있으면 되살리기
            ctypes.windll.user32.SetForegroundWindow(h)

    def window_ready(self):
        """창이 처음 뜰 때: 크기 맞추기(테두리 없는 창은 처음에 작게 생김) + 윈도우 11 둥근 모서리
        + 작업 표시줄에서 빼기 (오른쪽 아래 ✱ 버튼이 있으니 필요 없다)"""
        self._place(self._mode)
        h = self._hwnd()
        if h:
            try:
                pref = ctypes.c_int(2)   # DWMWCP_ROUND
                ctypes.windll.dwmapi.DwmSetWindowAttribute(h, 33, ctypes.byref(pref), 4)
            except Exception:
                pass
            self._hide_from_taskbar(h)

    def reveal(self):
        """숨긴 채로 만든 창을, 화면을 다 그린 뒤에 보여 준다 (켤 때 옛 화면이 잠깐 비치지 않게).
        보이기 전에 크기·자리·작업 표시줄 빼기를 끝내 두어서 깜빡임이 없다."""
        if self._revealed or not self._window:
            return
        self._revealed = True
        h = self._hwnd()
        if h:
            self._hide_from_taskbar(h)
            self._place(self._mode)
        self._window.show()

    @staticmethod
    def _hide_from_taskbar(h):
        u = ctypes.windll.user32
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
        style = u.GetWindowLongW(h, GWL_EXSTYLE)
        new = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        if new == style:
            return
        visible = u.IsWindowVisible(h)
        if visible:   # 보이는 창은 잠깐 숨겼다 보여야 작업 표시줄이 바뀐다 (숨긴 창이면 그냥 바꾼다)
            u.ShowWindow(h, 0)
        u.SetWindowLongW(h, GWL_EXSTYLE, new)
        if visible:
            u.ShowWindow(h, 5)

    def moved(self, x, y):
        """버튼을 끌어 옮기면 그 자리를 기억 (잦은 저장을 피해 0.5초 뒤 한 번)"""
        if self._mode != "button":
            return
        self._conf["bx"], self._conf["by"] = x, y
        if self._save_timer:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.5, core.save_json, (core.CONF_FILE, self._conf))
        self._save_timer.start()

    def toggle(self):
        """단축키: 화면이 버튼↔창 전환을 정한다"""
        self._emit("hotkey")

    def quit_app(self):
        self._really_quit = True
        if self._window:
            self._window.destroy()

    # ----- 알림 -----
    def _color_loop(self):
        """구글 연결이면 켜고 30초 뒤·1시간마다: 색 없는 반복 일정에 안 겹치는 색 주기 (구글 캘린더에도)"""
        time.sleep(30)
        while not self._really_quit:
            try:
                if self._store.kind == "google":
                    with self._lock:
                        n = self._store.color_series()
                    if n:
                        self._soon_at = 0
                        self._emit("changed")
            except Exception:
                pass
            time.sleep(3600)

    def _notify_loop(self):
        while not self._really_quit:
            try:
                self._check_reminders()
            except Exception:
                pass
            time.sleep(20)

    def _check_reminders(self):
        if not self._conf.get("notify", True):
            return
        now = core.now_local()
        if time.time() - self._soon_at > 180:   # 3분마다 다시 읽기 (바뀌면 바로)
            with self._lock:
                self._soon = self._store.list_range(now.date(), now.date() + datetime.timedelta(days=2))
            self._soon_at = time.time()
        for ev in self._soon:
            if not ev.get("time") or ev.get("done"):   # 이미 끝낸 일정은 알리지 않는다
                continue
            rem = ev.get("remind")
            if rem is None:
                rem = self._remind_default()
            if rem < 0:
                continue
            start = datetime.datetime.combine(core.valid_date(ev["date"]), core.valid_time(ev["time"]))
            key = f"{ev['id']}@{ev['date']} {ev['time']}"
            if key in self._fired or not (start - datetime.timedelta(minutes=rem) <= now < start + datetime.timedelta(minutes=2)):
                continue
            self._fired.add(key)
            left = int((start - now).total_seconds() // 60)
            when = "지금 시작" if left <= 0 else (f"{left}분 뒤" if left < 60 else f"{left // 60}시간 {left % 60}분 뒤".replace(" 0분", ""))
            end = ev.get("end_time") if ev.get("end_time") != ev["time"] or ev.get("end_date") != ev["date"] else ""   # 시작 시각만 있는 일정
            msg = " · ".join(x for x in (f"{ev['time']}~{end or ''}".rstrip("~"), ev.get("location")) if x)
            self._toast(f"{when} · {ev['title']}", msg)
            self._emit("reminder", {"title": ev["title"], "when": when, "id": ev["id"]})

    def _toast(self, title, msg):
        try:
            import winreg   # 알림 보낸 앱 이름이 'ClaudeCal'로 보이게 (사용자 영역에만 등록)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\AppUserModelId\ClaudeCal") as k:
                winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "ClaudeCal")
        except OSError:
            pass
        try:
            from winotify import Notification, audio
            n = Notification(app_id="ClaudeCal", title=title, msg=msg or " ", duration="long")
            n.set_audio(audio.Reminder, loop=False)
            n.show()
        except Exception:
            self._emit("toast_fallback", {"title": title, "msg": msg})

    # ----- 자동 업데이트 (updater.py) -----
    def _try_update(self):
        """새 버전이 있으면 받아서 파일을 바꿔 둔다. 결과 문구"""
        with self._update_lock:
            if self._update_ready:
                return "새 버전을 받아 뒀어요. 창을 접으면 다시 켜져요."
            r = updater.update(HERE, os.path.join(APP_DIR, "update_backup"))
            if not r:
                return "최신 버전이에요."
            self._update_ready = r
            return "새 버전을 받았어요. 창을 접으면 다시 켜져요."

    def _update_loop(self):
        """켜고 20초 뒤, 그다음 6시간마다 확인. 받아 둔 게 있으면 버튼으로 접혀 있을 때 조용히 다시 켠다"""
        last = time.time() - 6 * 3600 + 20
        while not self._really_quit:
            if self._update_ready and self._mode == "button":
                return self.restart()
            if self._conf.get("auto_update", True) and not updater.is_dev(HERE) and time.time() - last >= 6 * 3600:
                last = time.time()
                try:
                    if self._try_update().startswith("새") and self._mode != "button":
                        self._emit("update_ready", self._update_ready)
                except Exception as e:
                    core.save_json(os.path.join(APP_DIR, "update_error.json"), {"at": time.ctime(), "error": str(e)[:500]})
            time.sleep(15)

    def check_update(self):
        """설정의 [지금 확인]"""
        if updater.is_dev(HERE):
            return self._err(RuntimeError("개발 폴더라서 업데이트하지 않아요."))
        try:
            return self._ok(reply=self._try_update(), state=self._state())
        except Exception as e:
            return self._err(RuntimeError(f"업데이트를 확인하지 못했어요: {e}"))

    def restart(self):
        """새 프로세스를 띄우고(이 프로세스가 끝나길 기다렸다 켜짐) 나는 끈다"""
        if self._update_ready:
            core.save_json(os.path.join(APP_DIR, "updated.json"), self._update_ready)
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            exe = exe[:-10] + "pythonw.exe"
        args = [exe, os.path.join(HERE, "claude_cal.pyw"), "--wait-pid", str(os.getpid())]
        if self._mode == "button":
            args.append("--button")
        subprocess.Popen(args, cwd=HERE, creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | 새 프로세스 그룹
        self.quit_app()

    def start_background(self):
        threading.Thread(target=self._notify_loop, daemon=True).start()
        threading.Thread(target=self._warm_up, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()
        threading.Thread(target=self._color_loop, daemon=True).start()
        if IS_WIN:
            threading.Thread(target=self._hotkey_loop, daemon=True).start()
            threading.Thread(target=self._open_request_loop, daemon=True).start()

    def _open_request_loop(self):
        """이미 켜져 있을 때 claude_cal.pyw를 또 실행하면 그쪽이 이 이벤트를 울린다 → 창 펼치기"""
        k32 = ctypes.windll.kernel32
        k32.CreateEventW.restype = ctypes.c_void_p
        k32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        ev = k32.CreateEventW(None, False, False, OPEN_EVENT)
        while ev and not self._really_quit:
            if k32.WaitForSingleObject(ev, 1000) == 0:
                self._emit("expand")

    def _warm_up(self):
        """anthropic 패키지는 처음 불러오는 데 10초쯤 걸려서, 창이 뜬 뒤 미리 불러 둔다 (첫 입력이 빨라지게)"""
        if self._conf.get("api_key"):
            time.sleep(2)
            try:
                import anthropic  # noqa: F401
            except Exception:
                pass

    def _hotkey_loop(self):
        user32 = ctypes.windll.user32
        hk = self._conf["hotkey"]
        mods, vk = parse_hotkey(hk)
        if vk is None or not user32.RegisterHotKey(None, 1, mods | 0x4000, vk):
            time.sleep(2)
            self._emit("status", {"text": f"단축키 {hk} 등록 실패 — 다른 앱이 쓰는 중일 수 있어요. 설정에서 바꿔 주세요.", "warn": True})
            return
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:
                self.toggle()
