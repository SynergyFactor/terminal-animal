@echo off
REM Build TerminalVelocity.exe (Windows). Extra args are passed through, e.g. build.bat --cache skip
setlocal
cd /d "%~dp0"
call :pick_python || exit /b 1
"%PY%" build.py --os windows %*
exit /b %ERRORLEVEL%

:pick_python
REM Prefer the project's virtual environment, then the py launcher, then python on PATH.
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe" & exit /b 0)
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (set "PY=%%i" & exit /b 0)
where python >nul 2>nul && (set "PY=python" & exit /b 0)
echo Python 3.10+ not found. Install it from https://www.python.org/ and try again.
exit /b 1
