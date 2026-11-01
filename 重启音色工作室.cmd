@echo off
setlocal
pushd "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-studio.ps1"
if errorlevel 1 (
  popd
  pause
  exit /b 1
)
call "%~dp0scripts\launch.cmd"
popd
