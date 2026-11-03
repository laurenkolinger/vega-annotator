@echo off
rem Double-click this file to install VEGA Annotator for your account.
rem It needs no administrator rights. The steps are in setup.ps1 beside this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo VEGA Annotator was not installed. The reason is above.
  pause
  exit /b 1
)
echo.
echo VEGA Annotator is installed. It opens now, and there is a shortcut on your desktop.
rem A pause that also works with no console, where timeout refuses to run.
ping -n 6 127.0.0.1 > nul
exit /b 0
