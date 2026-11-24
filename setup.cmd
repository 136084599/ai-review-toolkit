@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto error
echo Installation completed. Run the start launcher.
pause
exit /b 0
:error
echo Installation failed. Python 3.12 x64 and internet access are required.
pause
exit /b 1
