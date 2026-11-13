# -*- coding: utf-8 -*-
"""다른 컴퓨터에서 위젯 개발 폴더를 되살린다 (또는 깃허브의 최신 위젯 코드로 맞춘다).

  1) 아무 데나 새 폴더 ClaudeCal 을 만들고, 그 안에서:
       git clone https://github.com/yamchaewoo/claudecal.git site
  2) python site/dev/setup_dev.py
  3) credentials.json 은 깃허브에 없다 → 원래 PC에서 비공개로(카톡 등) 받아 ClaudeCal 폴더에 넣기

이미 되살린 폴더에서 다시 실행하면 site/widget 의 최신 코드로 덮어쓴다 (git pull 한 뒤에).
덮어쓰기 전에 내용이 다른 파일은 _backup_날짜시각/ 에 남겨 둔다 — 아직 안 올린 고친 내용이 날아가지 않게.
"""
import os
import sys
import shutil
import hashlib
import datetime

DEV = os.path.dirname(os.path.abspath(__file__))     # …/ClaudeCal/site/dev
SITE = os.path.dirname(DEV)                          # …/ClaudeCal/site
ROOT = os.path.dirname(SITE)                         # …/ClaudeCal  (개발 폴더)
WIDGET = os.path.join(SITE, "widget")
DEV_FILES = ["make_release.py", "make_exe.py", "make_app_prompt.py", "icon.ico", "설치 방법 (먼저 읽기).txt"]


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    if os.path.basename(SITE) != "site":
        sys.exit(f"저장소를 'site'라는 이름으로 받아야 해요 (지금: {os.path.basename(SITE)}).\n"
                 "  새 폴더 ClaudeCal 안에서: git clone https://github.com/yamchaewoo/claudecal.git site")
    pairs = []   # (원본, 개발 폴더 위치, 보여 줄 이름)
    for root, _, files in os.walk(WIDGET):
        for f in files:
            if f == "version.json":   # 개발 폴더는 자동 업데이트를 안 받으니 필요 없다
                continue
            src = os.path.join(root, f)
            rel = os.path.relpath(src, WIDGET)
            pairs.append((src, os.path.join(ROOT, rel), rel.replace(os.sep, "/")))
    for f in DEV_FILES:
        pairs.append((os.path.join(DEV, f), os.path.join(ROOT, f), f))

    backup = os.path.join(ROOT, "_backup_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    new = same = changed = 0
    for src, dst, name in pairs:
        if os.path.exists(dst):
            if sha(dst) == sha(src):
                same += 1
                continue
            os.makedirs(os.path.dirname(os.path.join(backup, name)), exist_ok=True)
            shutil.copy2(dst, os.path.join(backup, name))
            changed += 1
            print(f"  바뀜: {name}  (예전 것은 {os.path.basename(backup)}/ 에)")
        else:
            new += 1
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    print(f"\n개발 폴더: {ROOT}")
    print(f"새로 놓음 {new}개 · 그대로 {same}개 · 최신으로 바꿈 {changed}개")
    if not os.path.exists(os.path.join(ROOT, "credentials.json")):
        print("\n※ credentials.json 이 없어요. 원래 PC의 ClaudeCal 폴더에서 비공개로 받아 여기에 넣어 주세요 (구글 연결에 필요).")
    print("\n다음: 필요한 부품 설치 →  python -m pip install -r requirements.txt")
    print("위젯 켜기 →  claude_cal.pyw 더블클릭  (이 폴더는 옆에 site/ 가 있어서 자동 업데이트를 안 받아요)")


if __name__ == "__main__":
    main()
