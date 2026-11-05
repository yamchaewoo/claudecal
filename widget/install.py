# -*- coding: utf-8 -*-
"""ClaudeCal 설치: 부품 설치 → 윈도우 켤 때 자동 실행 등록 → 위젯 실행. install.bat이 부른다.
(한글 안내는 여기서 한다 — .bat 안의 한글은 PC 설정에 따라 cmd가 깨져서 읽는다)"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    print("ClaudeCal 설치를 시작해요.\n")
    print("1/3 필요한 부품 설치 중... (처음엔 1~2분)")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "-q",
                        "-r", os.path.join(HERE, "requirements.txt")])
    if r.returncode != 0:
        print("\n[!] 부품 설치에 실패했어요. 인터넷 연결을 확인하고 install.bat 을 다시 실행해 주세요.")
        return 1

    # 방금 설치한 부품은 새 프로세스에서 불러와야 확실하다 (사용자 폴더에 처음 설치된 경우)
    print("2/3 윈도우 켤 때 자동 실행 등록 중...")
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import app_api; app_api.set_autostart(True)",
                        HERE], capture_output=True, text=True)
    if r.returncode != 0:
        print("    자동 실행 등록은 건너뛰었어요. 나중에 위젯 ⋯ → 설정에서 켤 수 있어요.")

    print("3/3 위젯 실행!")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen([pythonw if os.path.exists(pythonw) else sys.executable, os.path.join(HERE, "claude_cal.pyw")], cwd=HERE)
    print("\n화면 오른쪽 아래에 위젯이 떠요 (열고 닫기: Ctrl+Shift+Space).")
    print("⋯ → 설정에서 Claude API 키를 넣고 구글 캘린더를 연결해 주세요.")
    print("자세한 건 '설치 방법 (먼저 읽기).txt' 를 봐 주세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
