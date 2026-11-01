@echo off
setlocal
pushd "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist "engines\index\.venv\Scripts\python.exe" (
  echo IndexTTS environment was not found. Run the installer first.
  popd
  pause
  exit /b 1
)
"engines\index\.venv\Scripts\python.exe" -u "download_models.py" --bigvgan-only
set "downloadExit=%errorlevel%"
popd
pause
exit /b %downloadExit%
