@echo off
setlocal
pushd "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "appPython=%CD%\.venv\Scripts\python.exe"
if not exist "%appPython%" set "appPython=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%appPython%" (
  echo Python was not found. Run the installer first.
  popd
  pause
  exit /b 1
)
"%appPython%" -u "%CD%\app.py"
set "studioExit=%errorlevel%"
popd
if not "%studioExit%"=="0" pause
exit /b %studioExit%
