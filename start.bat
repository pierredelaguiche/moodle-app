@echo off
rem Double-click launcher for Windows. From PowerShell or cmd you can also run:  py start.py
cd /d "%~dp0"
where py >nul 2>nul && (py start.py %* & goto :end)
python start.py %*
:end
if errorlevel 1 pause
