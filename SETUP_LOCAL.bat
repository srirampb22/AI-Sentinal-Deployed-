@echo off
setlocal

cd /d "%~dp0"

echo [AI-Sentinal] Local setup started...

echo [1/5] Removing old local venv (if exists)...
if exist ".venv" rmdir /s /q ".venv"

echo [2/5] Creating .venv in project folder...
python -m venv .venv
if %ERRORLEVEL% NEQ 0 (
  echo Failed to create venv. Ensure Python 3.10+ is installed and on PATH.
  pause
  exit /b 1
)

echo [3/5] Upgrading pip/setuptools/wheel...
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
if %ERRORLEVEL% NEQ 0 (
  echo Failed to upgrade pip tools.
  pause
  exit /b 1
)

echo [4/5] Installing dependencies from requirements.txt...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
  echo Dependency install failed. Check error output above.
  pause
  exit /b 1
)

echo [5/5] Verifying core imports...
.venv\Scripts\python.exe -c "import flask, cv2, torch, torchvision, numpy; print('Core imports OK')"
if %ERRORLEVEL% NEQ 0 (
  echo Core import verification failed.
  pause
  exit /b 1
)

echo Setup complete.
echo Next step: run RUN_LOCAL.bat
pause