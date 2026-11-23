@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Creating Python environment...
  python -m venv .venv
)
echo Checking packages...
.venv\Scripts\python -m pip install -q -r requirements.txt
.venv\Scripts\python run.py
pause
