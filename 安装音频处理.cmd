@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Please install IndexTTS and the studio first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u "setup_audio.py"
pause
