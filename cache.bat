@echo off
REM Cache manager: run with no arguments for a menu, or e.g.  cache.bat --mode update
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (
  for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)
if not defined PY set "PY=python"
"%PY%" scripts\build_cache.py %*
exit /b %ERRORLEVEL%
