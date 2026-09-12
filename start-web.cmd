@echo off
setlocal
cd /d "%~dp0"

py -3.11 -c "import sys" >nul 2>&1
if %errorlevel%==0 (
  py -3.11 scripts\start_web.py %*
  exit /b %errorlevel%
)

python -c "import sys" >nul 2>&1
if %errorlevel%==0 (
  python scripts\start_web.py %*
  exit /b %errorlevel%
)

echo [TransitScholar] ERROR: Python 3.11+ was not found.
echo Install Python 3.11 or newer, then run start-web.cmd again.
exit /b 1
