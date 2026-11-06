# -*- coding: utf-8 -*-
"""폰 웹앱(site/app/prompt.js)에 위젯과 같은 Claude 지시문·응답 형식·모델 이름을 넣는다.
calcore.py의 SYSTEM_PROMPT / SCHEMA / MODELS를 바꾸면 이걸 다시 실행하고 site를 올린다."""
import os
import json

import calcore as core

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "app", "prompt.js")

with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write("// make_app_prompt.py가 calcore.py에서 만든 파일 — 직접 고치지 말 것\n")
    f.write(f"export const SYSTEM_PROMPT = {json.dumps(core.SYSTEM_PROMPT, ensure_ascii=False)};\n")
    f.write(f"export const SCHEMA = {json.dumps(core.SCHEMA, ensure_ascii=False)};\n")
    f.write(f"export const MODELS = {json.dumps(core.MODELS)};\n")
print("만듦:", OUT)
