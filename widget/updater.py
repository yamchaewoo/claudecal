# -*- coding: utf-8 -*-
"""자동 업데이트 — 파이썬 기본 기능만 쓴다 (따로 깔 부품 없음).

깃허브 페이지의 widget/version.json(버전표: 파일마다 sha256)을 받아 내 파일과 비교하고,
바뀐 파일만 받아서 확인(sha256)한 뒤 바꾼다. 하나라도 이상하면 아무것도 안 바꾸고 다음에 다시.
  · 개발 폴더(옆에 site 폴더가 있음)는 건드리지 않는다.
  · 바꾸기 전 파일은 %APPDATA%\\ClaudeCal\\update_backup\\ 에 남긴다.
  · requirements.txt가 바뀌면 부품 설치(pip)부터 하고, 실패하면 바꾸지 않는다.
새 버전 올리기는 개발 폴더의 make_release.py.
"""
import os
import sys
import json
import time
import shutil
import hashlib
import subprocess
import urllib.parse
import urllib.request

BASE = "https://yamchaewoo.github.io/claudecal/widget/"
VERSION_FILE = "version.json"   # 지금 깔린 버전 (버전표 그대로)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def is_dev(here):
    return os.path.isdir(os.path.join(here, "site"))


def local_version(here):
    if getattr(sys, "frozen", False):   # exe 판은 안에 든 판 번호
        from version import VERSION
        return VERSION
    try:
        with open(os.path.join(here, VERSION_FILE), encoding="utf-8") as f:
            return json.load(f).get("version", "")
    except (OSError, ValueError):
        return ""


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "ClaudeCal-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _file_sha(path):
    try:
        with open(path, "rb") as f:
            return sha256(f.read())
    except OSError:
        return None


def _pip(here, req_bytes):
    """새 requirements.txt로 부품 설치. 성공하면 True"""
    tmp = os.path.join(here, "requirements.new.txt")
    with open(tmp, "wb") as f:
        f.write(req_bytes)
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-11] + "python.exe"
    try:
        r = subprocess.run([exe, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", tmp],
                           capture_output=True, timeout=900, creationflags=0x08000000)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def check(here, base=BASE):
    """새 버전이 있으면 (버전표, 바뀐 파일 목록), 없으면 (버전표, [])"""
    man = json.loads(_get(base + "version.json?t=" + str(int(time.time()))).decode("utf-8"))
    changed = [p for p, h in man["files"].items() if _file_sha(os.path.join(here, *p.split("/"))) != h]
    return man, changed


def update(here, backup_dir, base=BASE):
    """업데이트를 받아서 바꾼다. 결과: None(이미 최신 또는 개발 폴더) 또는 {"version", "notes", "files"}.
    받다가 문제가 생기면 예외 (아무것도 안 바뀐 상태)."""
    if is_dev(here):
        return None
    man, changed = check(here, base)
    if not changed:
        if local_version(here) != man.get("version"):   # 파일은 같은데 버전표만 옛것
            with open(os.path.join(here, VERSION_FILE), "w", encoding="utf-8") as f:
                json.dump(man, f, ensure_ascii=False, indent=1)
        return None
    new = {}
    for p in changed:   # 내용 해시를 주소에 붙여서 캐시된 옛 파일을 받지 않게
        data = _get(base + urllib.parse.quote(p) + "?h=" + man["files"][p][:16])
        if sha256(data) != man["files"][p]:
            raise RuntimeError(f"받은 파일이 버전표와 달라요 ({p}) — 올린 직후라 아직 반영 중일 수 있어요.")
        new[p] = data
    if "requirements.txt" in new and not _pip(here, new["requirements.txt"]):
        raise RuntimeError("새 버전에 필요한 부품 설치(pip)에 실패했어요.")
    if os.path.isdir(backup_dir):
        shutil.rmtree(backup_dir, ignore_errors=True)
    for p, data in new.items():   # 옛 파일은 백업해 두고, .new로 다 쓴 다음 한꺼번에 바꾼다
        dst = os.path.join(here, *p.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            b = os.path.join(backup_dir, *p.split("/"))
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(dst, b)
        with open(dst + ".new", "wb") as f:
            f.write(data)
    for p in new:
        dst = os.path.join(here, *p.split("/"))
        os.replace(dst + ".new", dst)
    with open(os.path.join(here, VERSION_FILE), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    return {"version": man.get("version", ""), "notes": man.get("notes", ""), "files": sorted(new)}


# ================= exe 판 (2026-11-13~) =================
# 깃허브 yamchaewoo/claudecal-app 에 늘 최신 exe 하나 + version.json({version,url,size,sha256,notes}).
# exe로 켰으면: 새 판을 '<exe>.new'로 받아 두고, 다시 켤 때 맨 처음(swap_on_start)에 바꿔 넣는다.
#   (PyInstaller 한 파일 exe는 돌면서도 exe에서 모듈을 읽어서, 돌 때 그 자리에 새 exe를 넣으면 깨진다)
# 파이썬 판(옛 zip)으로 켰으면: 옆에 ClaudeCal.exe를 받아 두고 그걸로 갈아탄다.
import ctypes
from ctypes import wintypes as _wt

APP_URL = os.environ.get("CLAUDECAL_UPDATE_URL") or "https://raw.githubusercontent.com/yamchaewoo/claudecal-app/main/version.json"
EXE_NAME = "ClaudeCal.exe"
FROZEN = bool(getattr(sys, "frozen", False))
EXE = sys.executable if FROZEN else None


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def _file_sha_big(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def app_info():
    req = urllib.request.Request(f"{APP_URL}?t={int(time.time())}", headers={"User-Agent": "ClaudeCal-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def download_exe(info, dest):
    """info의 exe를 dest로 받는다 (크기·sha256이 맞을 때만, 이미 맞는 게 있으면 그대로)"""
    if os.path.exists(dest) and os.path.getsize(dest) == info["size"] and _file_sha_big(dest) == info["sha256"]:
        return dest
    part = dest + ".download"
    h, size = hashlib.sha256(), 0
    req = urllib.request.Request(info["url"], headers={"User-Agent": "ClaudeCal-updater"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
            size += len(chunk)
    if size != info["size"] or h.hexdigest() != info["sha256"]:
        os.remove(part)
        raise RuntimeError("받은 exe의 크기·검사값이 version.json과 달라요 — 올린 직후라 아직 반영 중일 수 있어요.")
    os.replace(part, dest)
    return dest


def exe_update(current):
    """exe 판: 새 판이면 '<exe>.new'로 받아 두고 {"version","notes"}, 아니면 None"""
    info = app_info()
    if _ver(info["version"]) <= _ver(current):
        return None
    download_exe(info, EXE + ".new")
    return {"version": info["version"], "notes": info.get("notes", "")}


def migrate_exe(here):
    """파이썬 판: 옆에 ClaudeCal.exe를 받아 둔다 (갈아타기). 받았으면 {"version","notes","exe"}"""
    info = app_info()
    path = download_exe(info, os.path.join(here, EXE_NAME))
    return {"version": info["version"], "notes": info.get("notes", "") or "이제 exe 하나로 돌아가요", "exe": path}


_k32 = ctypes.windll.kernel32
_k32.OpenProcess.restype = _wt.HANDLE
_k32.OpenProcess.argtypes = [_wt.DWORD, _wt.BOOL, _wt.DWORD]
_k32.WaitForSingleObject.argtypes = [_wt.HANDLE, _wt.DWORD]
_k32.CloseHandle.argtypes = [_wt.HANDLE]
_k32.QueryFullProcessImageNameW.argtypes = [_wt.HANDLE, _wt.DWORD, _wt.LPWSTR, ctypes.POINTER(_wt.DWORD)]


def split_wait_args(argv):
    """argv에서 '--wait-pid 1,2' 를 떼어 (나머지, [pid…])"""
    args, pids, it = [], [], iter(argv)
    for a in it:
        if a == "--wait-pid":
            pids = [int(p) for p in next(it, "").split(",") if p.isdigit()]
        else:
            args.append(a)
    return args, pids


def wait_pids(pids, timeout=30):
    end = time.time() + timeout
    for pid in pids:
        h = _k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if h:
            _k32.WaitForSingleObject(h, max(0, int((end - time.time()) * 1000)))
            _k32.CloseHandle(h)


def _others_running(exe):
    """이 exe 파일로 나(와 부모 — 한 파일 exe는 둘로 뜸) 말고 돌고 있는 게 있나"""
    pids, got = (_wt.DWORD * 4096)(), _wt.DWORD()
    ctypes.windll.psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(got))
    me = os.path.normcase(exe)
    for pid in pids[:got.value // 4]:
        if pid in (0, os.getpid(), os.getppid()):
            continue
        h = _k32.OpenProcess(0x1000, False, pid)
        if not h:
            continue
        try:
            buf, n = ctypes.create_unicode_buffer(1024), _wt.DWORD(1024)
            if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) and os.path.normcase(buf.value) == me:
                return True
        finally:
            _k32.CloseHandle(h)
    return False


def clean_env():
    """새 exe를 띄우기 전: 이 exe가 풀어 둔 임시 폴더를 새 exe가 이어 쓰지 않게"""
    for k in [k for k in os.environ if k.startswith(("_MEI", "_PYI"))]:
        del os.environ[k]
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"


def swap_on_start(argv):
    """exe로 켤 때 맨 처음. 받아 둔 새 판이 있으면 바꿔 넣고 새 exe를 띄운 뒤 True → 곧바로 끝낼 것"""
    if not EXE:
        return False
    args, pids = split_wait_args(argv)
    if pids:
        wait_pids(pids)
    new, old = EXE + ".new", EXE + ".old"
    try:
        os.remove(old)
    except OSError:
        pass
    if not os.path.exists(new):
        return False
    try:
        if _others_running(EXE):
            return False
        os.replace(EXE, old)   # 돌고 있는 나를 비킴 (이름 바꾸기는 된다)
    except Exception:  # noqa: BLE001
        return False
    try:
        os.replace(new, EXE)
    except OSError:
        os.replace(old, EXE)
        return False
    clean_env()   # 여기부터 모듈을 더 읽으면 안 됨 (exe 자리에 새 판)
    os.startfile(EXE, "open", subprocess.list2cmdline(args))
    return True


def launch(exe, args):
    """exe를 따로(이 프로세스와 상관없이) 띄운다"""
    clean_env()
    subprocess.Popen([exe, *args], cwd=os.path.dirname(exe), creationflags=0x00000008 | 0x00000200, close_fds=True)
