@echo off
rem NeuroNest: start everything.  Double-click, or from PowerShell:
rem   .\run_all.bat            server + dashboard (webcam off; turn it on from the dashboard's Webcam button)
rem   .\run_all.bat B          same, starting with patient B
rem   .\run_all.bat A cam      also start the webcam tracker now (camera 0)
rem   .\run_all.bat A cam 1    webcam tracker on camera 1 (e.g. a USB webcam)
rem The Uno Q connects by itself (run the neuronest app in Arduino App Lab once).
cd /d "%~dp0"
set CARD=%1
if "%CARD%"=="" set CARD=A
set CAMOPT=%2
set CAM=%3
if "%CAM%"=="" set CAM=0

powershell -NoProfile -Command "try { Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  echo Server already running on port 8000 - using it.
) else (
  echo Starting the NeuroNest server ^(patient %CARD%^)...
  start "NeuroNest server" cmd /k python "%~dp0web\dashboard\dev_server.py" --card %CARD%
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

if /i "%CAMOPT%"=="cam" (
  echo Starting the webcam tracker ^(camera %CAM%^)...
  start "NeuroNest webcam" cmd /c ""%~dp0run_hands.bat" %CAM%"
) else (
  echo Webcam is off. Turn it on any time with the dashboard's Webcam button.
)

echo.
echo Running:
echo   Dashboard : http://localhost:8000/dashboard/
echo   Phones    : http://^<laptop-ip^>:8000/phone/   (QR code: http://localhost:8000/qr)
echo   To stop   : close the "NeuroNest server" window.
timeout /t 8 >nul
