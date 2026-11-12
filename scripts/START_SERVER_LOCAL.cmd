@echo off
setlocal
title Overwatch 1.74 - Local Server
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
if defined ORPHEUS_SERVER_PROFILE goto prepared
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
set "ORPHEUS_PYTHON=%~dp0.venv\Scripts\python.exe"
goto checkports
:prepared
if not defined ORPHEUS_PYTHON set "ORPHEUS_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%ORPHEUS_PYTHON%" goto failed
:checkports
for %%P in (1118 21119 3724 3725) do (
  "%ORPHEUS_PYTHON%" -c "import socket,sys; s=socket.socket(); s.settimeout(0.3); sys.exit(0 if s.connect_ex(('127.0.0.1',int(sys.argv[1])))==0 else 1)" %%P
  if not errorlevel 1 goto busy
)
echo Local server only. Closing this window stops the services.
echo Dashboard: http://127.0.0.1:3725
if defined ORPHEUS_SERVER_PROFILE (
  "%ORPHEUS_PYTHON%" -u -B tools\start_prepared_server.py "%ORPHEUS_SERVER_PROFILE%" %* --mode retail --no-launch --host 127.0.0.1 --bnet-port 1118 --port 3724 --dashboard-port 3725 --game-port 3730 --test-players 1
) else (
  "%ORPHEUS_PYTHON%" -u -B -m ow174 %* --mode retail --no-launch --host 127.0.0.1 --bnet-port 1118 --port 3724 --dashboard-port 3725 --game-port 3730 --test-players 1
)
if errorlevel 1 goto failed
exit /b 0
:busy
echo A required local TCP port is already in use. No server was started.
echo Close the existing server before starting this entry point.
exit /b 1
:failed
echo Startup failed. See logs\ow174.log for details.
exit /b 1
