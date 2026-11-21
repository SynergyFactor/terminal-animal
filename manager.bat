@echo off
REM Opens the Terminal Velocity Manager (GUI for cache, fallback data, build and tests).
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (start "" ".venv\Scripts\pythonw.exe" manager.py & exit /b 0)
where pyw >nul 2>nul && (start "" pyw -3 manager.py & exit /b 0)
start "" pythonw manager.py
