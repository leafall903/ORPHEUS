@echo off
call "%~dp0START.bat" %* --mode join-tournament
exit /b %errorlevel%
