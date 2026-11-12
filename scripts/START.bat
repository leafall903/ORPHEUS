@echo off
setlocal
rem Starts the lobby server and the game. Options: START.bat --help
title Overwatch 1.74 lobby server
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"
py -3.12 -c "pass" >nul 2>nul || (
  echo Python is not installed. Install Python 3.12 ^(64-bit^) from python.org, then start again.
  start "" https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
py -3.12 -B -m orpheus %*
set "ORPHEUS_EXIT=%ERRORLEVEL%"
if not "%ORPHEUS_EXIT%"=="0" if "%~1"=="" pause
exit /b %ORPHEUS_EXIT%
