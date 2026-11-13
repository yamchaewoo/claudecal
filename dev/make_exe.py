# -*- coding: utf-8 -*-
"""ClaudeCal.exe 굽기 (개발 폴더에서): python make_exe.py  → 배포/ClaudeCal.exe

- exe 하나에 파이썬·부품·화면(ui)·구글 열쇠(credentials.json)가 다 들어간다 (받는 사람은 파이썬 필요 없음).
- 굽는 중간 파일은 원드라이브에 쌓이지 않게 임시 폴더에서.
- 배포/ClaudeCal.exe가 켜져 있으면 덮어쓸 수 없어서 임시 폴더의 ClaudeCal_새판.exe로 남긴다.
필요: pip install pyinstaller
"""
import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "배포")
WORK = os.path.join(tempfile.gettempdir(), "ClaudeCal_굽기")
NAME = "ClaudeCal"


def build():
    """exe를 굽고 그 경로를 돌려준다"""
    import PyInstaller.__main__
    cred = os.path.join(HERE, "credentials.json")
    if not os.path.exists(cred):
        sys.exit("credentials.json이 없어요 — exe 안에 넣어야 받은 사람이 바로 구글 연결을 할 수 있어요.\n"
                 "원래 PC의 ClaudeCal 폴더에서 비공개로 받아 이 폴더에 넣고 다시 하세요.")
    built = os.path.join(WORK, "dist")
    PyInstaller.__main__.run([
        os.path.join(HERE, "claude_cal.pyw"),
        "--name", NAME,
        "--onefile", "--windowed", "--noconfirm", "--clean",
        "--icon", os.path.join(HERE, "icon.ico"),
        "--add-data", os.path.join(HERE, "ui") + os.pathsep + "ui",
        "--add-data", cred + os.pathsep + ".",
        "--distpath", built, "--workpath", WORK, "--specpath", WORK,
        "--paths", HERE,
        "--hidden-import", "version", "--hidden-import", "app_api",
        "--collect-data", "googleapiclient",   # 구글 API 설명 파일 (없으면 build()가 실패)
        "--collect-all", "winotify",           # 윈도우 알림 (안에 든 powershell 스크립트)
        "--collect-data", "tzdata",
        "--exclude-module", "tkinter",
    ])
    exe = os.path.join(built, NAME + ".exe")
    os.makedirs(DIST, exist_ok=True)
    dst = os.path.join(DIST, NAME + ".exe")
    try:
        shutil.copy2(exe, dst)
    except PermissionError:
        dst = os.path.join(tempfile.gettempdir(), NAME + "_새판.exe")
        shutil.copy2(exe, dst)
        print(f"\n배포/{NAME}.exe가 켜져 있어 바꾸지 못했어요. 새 판은 여기: {dst}")
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n완료: {dst}  ({os.path.getsize(dst) / 1e6:.1f}MB)")
    return dst


if __name__ == "__main__":
    build()
