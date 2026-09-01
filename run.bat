@echo off
REM Double-clickable launcher. Works from any directory.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo JobPilot is not set up yet.
  echo Run setup first:
  echo   powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
  echo.
  pause
  exit /b 1
)

echo.
echo Starting JobPilot - your browser will open at http://127.0.0.1:8765
echo Keep THIS WINDOW OPEN while you use it. Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m app.main

REM Always pause, whatever happened. If the server exits for any reason -- an
REM error, a port clash, or Ctrl+C -- the reason is on screen above, and the
REM window closing instantly is what makes it look like nothing ran.
echo.
echo ---------------------------------------------------------------
echo JobPilot has stopped. The reason is shown above.
echo ---------------------------------------------------------------
pause
