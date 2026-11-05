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
