@echo off
REM Guided upload to GitHub. Add --check to run all checks without uploading.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (
  for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)
if not defined PY set "PY=python"
"%PY%" publish.py %*
echo.
pause
