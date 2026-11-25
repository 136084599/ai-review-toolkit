@echo off
setlocal
pushd "%~dp0" || exit /b 1
runtime\python.exe start.py
set "RESULT=%errorlevel%"
popd
if not "%RESULT%"=="0" pause
exit /b %RESULT%
