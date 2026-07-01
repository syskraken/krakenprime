@echo off
cd /d "%~dp0"
set "SCRIPT=%~dp0gui_app.py"
start "" pythonw "%SCRIPT%" %*
exit /b
