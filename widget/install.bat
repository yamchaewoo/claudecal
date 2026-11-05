@echo off
rem ClaudeCal installer. Keep this file ASCII only: Korean text here breaks cmd on many PCs.
rem The real work (and Korean messages) is in install.py.
cd /d "%~dp0"
set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  echo.
  echo [!] Python is not installed.
  echo     Get it from https://www.python.org/downloads/
  echo     and CHECK "Add python.exe to PATH" on the first screen, then run this again.
  echo.
  pause
  exit /b 1
)
%PY% "%~dp0install.py"
echo.
pause
