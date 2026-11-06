# -*- coding: utf-8 -*-
"""위젯 새 버전 올리기 (개발 폴더에서만 씀)

  python make_release.py "바뀐 점 한 줄"
  python make_release.py --dev        (새 버전 없이 개발 도구만 site/dev/ 에 복사)

1) site/widget/ 에 위젯 파일과 version.json(파일마다 sha256)을 만든다
2) 친구에게 줄 PJ/ClaudeCal_배포.zip 도 새로 만든다 (version.json·credentials.json·설치 안내 포함)
3) 개발 도구(이 파일·make_app_prompt.py·설치 안내)를 site/dev/ 에 복사 → 다른 컴퓨터에서 site/dev/setup_dev.py로 개발 폴더를 되살린다
그다음 site 폴더에서 커밋·푸시하면, 친구들 위젯이 6시간 안에 알아서 받아 간다 (updater.py).
credentials.json은 깃허브에 올리지 않는다.
"""
import os
import sys
import json
import shutil
import zipfile
import datetime

import updater

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
OUT = os.path.join(SITE, "widget")
ZIP = os.path.join(os.path.dirname(HERE), "ClaudeCal_배포.zip")
FILES = ["claude_cal.pyw", "app_api.py", "calcore.py", "stores.py", "updater.py", "ui/index.html",
         "requirements.txt", "README.md", "install.py", "install.bat"]          # 자동 업데이트로 바뀌는 파일
ZIP_ONLY = ["설치 방법 (먼저 읽기).txt", "credentials.json"]                     # 처음 나눠 줄 때만
DEV_FILES = ["make_release.py", "make_app_prompt.py", "설치 방법 (먼저 읽기).txt"]   # site/dev/ 에도 올리는 개발 도구 (비밀 없음)


def sync_dev():
    """개발 도구를 site/dev/ 에 복사 (깃허브에 같이 올라가서 다른 컴퓨터에서 개발 폴더를 되살릴 수 있게)"""
    dev = os.path.join(SITE, "dev")
    os.makedirs(dev, exist_ok=True)
    for p in DEV_FILES:
        shutil.copyfile(os.path.join(HERE, p), os.path.join(dev, p))


def next_version():
    today = datetime.date.today().strftime("%Y.%m.%d")
    try:
        with open(os.path.join(OUT, "version.json"), encoding="utf-8") as f:
            old = json.load(f)["version"]
    except (OSError, ValueError, KeyError):
        old = ""
    n = int(old.rsplit(".", 1)[1]) + 1 if old.startswith(today + ".") else 1
    return f"{today}.{n}"


def main():
    if sys.argv[1:] == ["--dev"]:
        sync_dev()
        print("개발 도구를 site/dev/ 에 복사했어요. site 폴더에서 커밋·푸시하세요.")
        return
    notes = " ".join(sys.argv[1:]).strip()
    with open(os.path.join(HERE, "install.bat"), "rb") as f:
        if any(b >= 128 for b in f.read()):
            sys.exit("install.bat에 한글(ASCII 아닌 글자)이 있어요 — 친구 PC cmd에서 깨져요. 빼고 다시 하세요.")
    man = {"version": next_version(), "notes": notes, "files": {}}
    keep = {os.path.normcase(os.path.join(OUT, *p.split("/"))) for p in FILES + ["version.json"]}
    for root, _, files in os.walk(OUT):   # 폴더째 지우면 OneDrive가 잡고 있어 실패할 때가 있어서, 목록에 없는 파일만 지우고 나머지는 덮어쓴다
        for f in files:
            if os.path.normcase(os.path.join(root, f)) not in keep:
                os.remove(os.path.join(root, f))
    for p in FILES:
        src = os.path.join(HERE, *p.split("/"))
        dst = os.path.join(OUT, *p.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        with open(src, "rb") as f:
            man["files"][p] = updater.sha256(f.read())
    with open(os.path.join(OUT, "version.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)

    sync_dev()
    tmp = ZIP + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in ZIP_ONLY[:1] + FILES + ZIP_ONLY[1:]:
            src = os.path.join(HERE, *p.split("/"))
            if p == "credentials.json" and not os.path.exists(src):   # 다른 컴퓨터엔 없을 수 있다 (깃허브에 안 올리니까)
                print("※ credentials.json이 없어서 zip에 못 넣었어요. 친구에게 줄 zip이면 원래 PC에서 받아 이 폴더에 넣고 다시 하세요.")
                continue
            z.write(src, "ClaudeCal/" + p)
        z.writestr("ClaudeCal/version.json", json.dumps(man, ensure_ascii=False, indent=1))
    os.replace(tmp, ZIP)
    size = sum(os.path.getsize(os.path.join(OUT, *p.split("/"))) for p in FILES)
    print(f"버전 {man['version']} 준비 끝 — 위젯 파일 {len(FILES)}개 ({size // 1024}KB), 배포 zip {os.path.getsize(ZIP) // 1024}KB")
    print("이제 site 폴더에서 커밋·푸시하면 친구들 위젯이 알아서 받아요.")


if __name__ == "__main__":
    main()
