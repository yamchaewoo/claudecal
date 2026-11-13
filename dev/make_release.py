# -*- coding: utf-8 -*-
"""위젯 새 판 올리기 (개발 폴더에서만 씀)

  python make_release.py "바뀐 점 한 줄"     → 끝에 보여 주는 두 가지를 실행
  python make_release.py --dev               (새 판 없이 개발 도구만 site/dev/ 에 복사)

1) version.py를 오늘 날짜 판으로 올린다 (같은 날 또 올리면 .2)
2) exe를 굽는다 (make_exe.py — credentials.json이 exe 안에 들어간다)
3) 릴리스 저장소(%LOCALAPPDATA%\\ClaudeCal_릴리스 = 깃허브 yamchaewoo/claudecal-app)를 옛 판 기록 없이 새로 짠다:
   [exe·README 커밋] → [그 커밋 번호로 고정한 exe 주소를 적은 version.json 커밋]  → 강제 푸시 한 줄을 보여 줌
   (저장소는 늘 최신 판 하나. 주소가 커밋 번호로 고정이라 캐시 때문에 version.json과 exe가 엇갈리지 않는다)
4) site/widget/ 에도 위젯 파이썬 파일과 version.json을 쓴다 — 옛 파이썬 판(zip)이 이걸 받아 exe로 갈아타고,
   다른 컴퓨터에서 개발 폴더를 되살릴 때(site/dev/setup_dev.py)도 쓴다 → site에서 커밋·푸시
credentials.json은 site(깃허브 페이지)에는 올리지 않는다 (exe 안에만).
"""
import os
import re
import sys
import json
import shutil
import hashlib
import datetime
import subprocess

import updater

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
OUT = os.path.join(SITE, "widget")
FILES = ["claude_cal.pyw", "app_api.py", "calcore.py", "stores.py", "updater.py", "version.py", "ui/index.html",
         "requirements.txt", "README.md", "install.py", "install.bat"]          # 파이썬 판 자동 업데이트 파일
DEV_FILES = ["make_release.py", "make_exe.py", "make_app_prompt.py", "icon.ico", "설치 방법 (먼저 읽기).txt"]   # site/dev/ 에도 (비밀 없음)

REPO = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ClaudeCal_릴리스")
REMOTE = "https://github.com/yamchaewoo/claudecal-app.git"
ASSET = "ClaudeCal.exe"
RAW = "https://raw.githubusercontent.com/yamchaewoo/claudecal-app/{ref}/" + ASSET
TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
PUSH = f'git -C "{REPO}" push -f origin main'   # 올리기 명령 — 글자 그대로 실행 (사용자 설정에 이 한 줄만 허용)


def sync_dev():
    dev = os.path.join(SITE, "dev")
    os.makedirs(dev, exist_ok=True)
    for p in DEV_FILES:
        shutil.copyfile(os.path.join(HERE, p), os.path.join(dev, p))


def bump():
    p = os.path.join(HERE, "version.py")
    s = open(p, encoding="utf-8").read()
    old = re.search(r'VERSION = "([\d.]+)"', s).group(1)
    today = datetime.date.today().strftime("%Y.%m.%d")
    n = int(old.rsplit(".", 1)[1]) + 1 if old.startswith(today + ".") else 1
    new = f"{today}.{n}"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s.replace(f'VERSION = "{old}"', f'VERSION = "{new}"'))
    return new


def write_widget(version, notes):
    man = {"version": version, "notes": notes, "files": {}}
    keep = {os.path.normcase(os.path.join(OUT, *p.split("/"))) for p in FILES + ["version.json"]}
    for root, _, files in os.walk(OUT):   # 폴더째 지우면 OneDrive가 잡고 있어 실패할 때가 있어서, 목록에 없는 파일만 지운다
        for f in files:
            if os.path.normcase(os.path.join(root, f)) not in keep:
                os.remove(os.path.join(root, f))
    for p in FILES:
        src, dst = os.path.join(HERE, *p.split("/")), os.path.join(OUT, *p.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        with open(src, "rb") as f:
            man["files"][p] = updater.sha256(f.read())
    with open(os.path.join(OUT, "version.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def commit_release(exe, version, notes):
    if not os.path.isdir(os.path.join(REPO, ".git")):
        os.makedirs(REPO, exist_ok=True)
        git("init", "-q")
        git("remote", "add", "origin", REMOTE)
        git("config", "user.name", "yamchaewoo")
        git("config", "user.email", "yamchaewoo@users.noreply.github.com")
        git("config", "core.autocrlf", "false")
    git("checkout", "-q", "--orphan", f"release-{version}")   # 옛 판 기록 없이 새로
    git("rm", "-rq", "--cached", "--ignore-unmatch", ".")
    for name in os.listdir(REPO):
        if name != ".git":
            path = os.path.join(REPO, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    shutil.copy2(exe, os.path.join(REPO, ASSET))
    shutil.copy2(os.path.join(SITE, "app", "icon-192.png"), os.path.join(REPO, "logo.png"))
    with open(os.path.join(REPO, "README.md"), "w", encoding="utf-8") as f:
        f.write(f"""<img src="logo.png" width="96" alt="">

# ClaudeCal

"11/20 금 3시 조합론 시험"처럼 한 줄로 적으면 일정이 되는 윈도우 위젯이에요. 구글 캘린더와 이어져요.

## ⬇ [ClaudeCal 내려받기 (누르면 바로 받아져요)]({RAW.format(ref="main")})

1. 위 링크를 누르면 `{ASSET}` 파일 하나가 받아져요. 압축 풀기도, 파이썬 설치도 없어요.
2. 찾기 쉬운 폴더(예: 문서)에 옮겨 두고 더블클릭하세요.
   - 브라우저가 "흔히 다운로드되지 않는 파일"이라고 하면 **[유지]**를 누르세요.
   - 처음 켤 때 "Windows의 PC 보호" 창이 뜨면 **[추가 정보] → [실행]**을 누르세요.
3. ⋯ → 설정에서 Claude API 키를 넣고, 구글 캘린더를 연결하세요. 윈도우 켤 때 같이 켜기도 거기서 켜요.
4. 그다음부터는 새 판이 나오면 알아서 받아 두고, 창을 접어 둔 사이에 조용히 다시 켜져요.

- 지금 판: **{version}** — {notes}
- 폰에서는 https://yamchaewoo.github.io/claudecal/app/
- 프로그램이 새 판을 확인하는 파일: `version.json`
""")
    git("add", "-A")
    git("commit", "-q", "-m", f"ClaudeCal {version} exe{TRAILER}")
    sha = git("rev-parse", "HEAD")
    data = open(exe, "rb").read()
    info = {"version": version, "url": RAW.format(ref=sha), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "notes": notes,
            "date": datetime.datetime.now().isoformat(timespec="minutes")}
    with open(os.path.join(REPO, "version.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    git("add", "version.json")
    git("commit", "-q", "-m", f"ClaudeCal {version}: {notes}{TRAILER}")
    git("branch", "-M", "main")
    for b in git("branch", "--format=%(refname:short)").split():
        if b != "main":
            git("branch", "-D", b)
    git("reflog", "expire", "--expire=now", "--all")   # 옛 판 exe가 이 컴퓨터 저장소에 쌓이지 않게
    git("gc", "-q", "--prune=now")
    return info


def main():
    if sys.argv[1:] == ["--dev"]:
        sync_dev()
        print("개발 도구를 site/dev/ 에 복사했어요. site 폴더에서 커밋·푸시하세요.")
        return
    notes = " ".join(a for a in sys.argv[1:] if not a.startswith("--")).strip()
    if not notes:
        sys.exit(__doc__)
    with open(os.path.join(HERE, "install.bat"), "rb") as f:
        if any(b >= 128 for b in f.read()):
            sys.exit("install.bat에 한글(ASCII 아닌 글자)이 있어요 — 친구 PC cmd에서 깨져요. 빼고 다시 하세요.")
    version = bump()
    print(f"판 번호: {version}")
    import make_exe
    exe = make_exe.build()
    info = commit_release(exe, version, notes)
    write_widget(version, notes)
    sync_dev()
    print(f"\n릴리스 저장소에 담음: {REPO}\n  {info['url']}\n  {info['size']:,}바이트 sha256 {info['sha256'][:16]}…")
    print(f"\n1) exe 올리기 (강제 푸시 — 저장소는 늘 최신 판 하나):\n{PUSH}")
    print('2) site 폴더에서 widget/·dev/ 커밋·푸시 (옛 파이썬 판이 exe로 갈아타는 데 씀)')


if __name__ == "__main__":
    main()
