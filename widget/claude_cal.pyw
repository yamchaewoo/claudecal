# -*- coding: utf-8 -*-
"""
ClaudeCal — 항상 떠 있는 '한 줄 입력' 일정 위젯 (Windows). 더블클릭으로 실행.
  · "11/20 금 3시 조합론 시험" 처럼 대충 적고 Enter → 등록 (단순한 건 바로, 나머지는 Claude가 해석)
  · 목록 / 주간 / 월간 보기, 반복 일정, 일정 전 윈도우 알림, 되돌리기(Ctrl+Z)
  · 숨기기/보이기: 단축키 (기본 Ctrl+Shift+Space)

화면은 ui/index.html, 기능은 app_api.py, 해석은 calcore.py, 저장은 stores.py (PC 또는 구글 캘린더).
"""
import os
import sys
import ctypes
import tempfile
import traceback

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))   # 프로그램 폴더
ROOT = getattr(sys, "_MEIPASS", HERE)   # 화면(ui) 파일이 있는 곳 — exe면 exe가 풀어 둔 임시 폴더
sys.path.insert(0, ROOT)
ERROR_LOG = os.path.join(tempfile.gettempdir(), "ClaudeCal_오류.txt")


def message(text):
    return ctypes.windll.user32.MessageBoxW(0, text, "ClaudeCal", 0x10 | 0x40000)


def wait_old_process():
    """다시 켤 때(--wait-pid 1,2): 옛 프로세스가 끝날 때까지 기다린다 (exe 판은 swap_on_start가 함)"""
    import updater
    _, pids = updater.split_wait_args(sys.argv[1:])
    if pids:
        updater.wait_pids(pids, 20)


def to_exe():
    """파이썬 판인데 옆에 ClaudeCal.exe(갈아탈 새 판)가 있으면 그걸 켜고 True — 개발 폴더는 빼고"""
    if FROZEN or os.path.isdir(os.path.join(HERE, "site")):
        return False
    exe = os.path.join(HERE, "ClaudeCal.exe")
    if not os.path.exists(exe):
        return False
    import updater
    updater.launch(exe, sys.argv[1:])
    return True


def single_instance():
    """이미 켜져 있으면 그쪽에 '창 펼치기'를 부탁하고 끝낸다"""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW(None, False, "ClaudeCalWidgetMutex")
    if k32.GetLastError() != 183:
        return True
    k32.OpenEventW.restype = ctypes.c_void_p
    k32.SetEvent.argtypes = k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    ev = k32.OpenEventW(0x0002, False, "ClaudeCalOpenRequest")   # EVENT_MODIFY_STATE
    if ev:
        k32.SetEvent(ev)
        k32.CloseHandle(ev)
    hwnd = ctypes.windll.user32.FindWindowW(None, "ClaudeCal")
    if hwnd:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    return False


def main():
    import importlib.util
    missing = [] if FROZEN else [pkg for mod, pkg in (("webview", "pywebview"), ("anthropic", "anthropic"), ("dateutil", "python-dateutil"),
                                    ("googleapiclient", "google-api-python-client"),
                                    ("google_auth_oauthlib", "google-auth-oauthlib"))
               if importlib.util.find_spec(mod) is None]   # 불러오지 않고 있는지만 (anthropic은 불러오는 데 10초쯤 걸림)
    if missing:
        message("프로그램 부품이 설치되어 있지 않아요.\n\ninstall.bat을 다시 실행하거나, 명령 프롬프트(cmd)에서\n\n"
                f"pip install {' '.join(missing)}\n\n을 실행한 뒤 다시 여세요.")
        return
    import webview
    import app_api

    api = app_api.Api()
    conf = api._conf
    # 윈도우 켤 때(시작 프로그램, --button)는 오른쪽 아래 버튼으로만, 직접 실행하면 창으로
    api._mode = "button" if "--button" in sys.argv else ("small" if conf.get("view", "list") == "list" else "big")
    x, y, w, h = api.geometry(api._mode)
    window = webview.create_window(app_api.WINDOW_TITLE, os.path.join(ROOT, "ui", "index.html"), js_api=api,
                                   width=w, height=h, x=x, y=y, frameless=True, easy_drag=False, shadow=True,
                                   on_top=bool(conf.get("on_top", True)), min_size=(40, 40),
                                   background_color="#1b1c21", text_select=True,
                                   hidden=True)   # 화면을 다 그린 뒤 api.reveal()로 보여 준다 (켤 때 옛 화면이 비치지 않게)
    api._window = window

    def on_closing():
        """Alt+F4 등으로 닫으면 끄지 않고 버튼으로 줄인다 (끄기는 ⋯ 메뉴의 '끄기')"""
        if api._really_quit or not api._ui_ready:
            return True
        import threading
        threading.Thread(target=api._emit, args=("collapse",), daemon=True).start()
        return False

    def on_moved(x, y):
        api.moved(x, y)

    def on_shown():
        api.window_ready()

    def on_loaded():   # 화면 쪽이 reveal()을 못 부르더라도 8초 뒤엔 창을 보여 준다
        import threading
        threading.Timer(8, api.reveal).start()

    window.events.closing += on_closing
    window.events.moved += on_moved
    window.events.shown += on_shown
    window.events.loaded += on_loaded
    api.start_background()
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    if FROZEN:
        import updater
        if updater.swap_on_start(sys.argv[1:]):   # 받아 둔 새 판으로 바꿔 넣고 새 exe를 띄웠음 → 바로 끝
            sys.exit(0)
    elif to_exe():
        sys.exit(0)
    else:
        wait_old_process()
    if not single_instance():
        sys.exit(0)
    try:
        main()
    except Exception:  # noqa: BLE001 — 창 없이 실행되므로 오류를 파일과 알림창으로 보여 준다
        with open(ERROR_LOG, "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        message(f"실행 중 오류가 났어요.\n\n{traceback.format_exc(limit=3)}\n\n자세한 내용: {ERROR_LOG}")
