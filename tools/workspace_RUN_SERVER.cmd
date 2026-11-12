@echo off
setlocal
cd /d "%~dp0verification\combined-server"
set "ORPHEUS_SERVER_PROFILE=%~dp0verification\parallel-merge\runtime-inputs-v15\environment.json"
set "ORPHEUS_PYTHON=%~dp0ORPHEUS\.venv\Scripts\python.exe"
call START_SERVER_LOCAL.cmd --data-dir "%~dp0ORPHEUS" %*
if errorlevel 1 pause
