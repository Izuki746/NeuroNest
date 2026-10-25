@echo off
rem NeuroNest webcam squeeze tracker. Double-click, or run from a terminal:  run_hands.bat [camera]
rem First run creates its own Python environment (.venv-hands) and installs MediaPipe.
cd /d "%~dp0"
if not exist ".venv-hands\Scripts\python.exe" (
  echo Setting up the webcam tracker environment ^(first run only^)...
  python -m venv .venv-hands || goto :fail
  ".venv-hands\Scripts\python.exe" -m pip install -q -r requirements-hands.txt || goto :fail
)
set CAM=%1
if "%CAM%"=="" set CAM=0
".venv-hands\Scripts\python.exe" inputs\hands.py --server ws://127.0.0.1:8000/ws/input --camera %CAM%
goto :eof
:fail
echo Setup failed. Is Python 3.10+ installed and on PATH?
pause
