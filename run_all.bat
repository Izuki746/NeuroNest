@echo off
rem NeuroNest: start everything. Double-click, or:  run_all.bat [A|B] [camera]
rem   1. server (brain + dashboard + phone page + Uno Q feed + voice + Tiger Data) in its own window
rem   2. waits until it answers, then opens the dashboard
rem   3. webcam squeeze tracker in its own window
rem The Uno Q connects by itself (run the neuronest app in Arduino App Lab once).
cd /d "%~dp0"
set CARD=%1
if "%CARD%"=="" set CARD=A
set CAM=%2
if "%CAM%"=="" set CAM=0

powershell -NoProfile -Command "try { Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  echo Server already running on port 8000 - using it.
) else (
  echo Starting the NeuroNest server ^(patient %CARD%^)...
  start "NeuroNest server" cmd /k python web\dashboard\dev_server.py --card %CARD%
  echo Waiting for the server to be ready...
  powershell -NoProfile -Command "for ($i = 0; $i -lt 90; $i++) { try { Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
  if errorlevel 1 (
    echo The server did not start. Check the "NeuroNest server" window for the error.
    pause
    exit /b 1
  )
)

echo Opening the dashboard...
start "" http://localhost:8000/dashboard/

echo Starting the webcam tracker ^(camera %CAM%^)...
start "NeuroNest webcam" cmd /c ""%~dp0run_hands.bat" %CAM%"

echo.
echo All running:
echo   Dashboard : http://localhost:8000/dashboard/
echo   Phones    : http://^<laptop-ip^>:8000/phone/   (QR code: http://localhost:8000/qr)
echo   To stop   : close the "NeuroNest server" window, and press Q in the webcam window.
timeout /t 8 >nul
