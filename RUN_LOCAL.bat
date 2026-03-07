@echo off
setlocal

cd /d "%~dp0"

echo [AI-Sentinal] Starting local server...

if exist ".venv\Scripts\python.exe" (
  echo [AI-Sentinal] Using venv: .venv
  .venv\Scripts\python.exe app.py
  goto :eof
)

echo [AI-Sentinal] .venv not found in this folder.
echo Run SETUP_LOCAL.bat once, then run RUN_LOCAL.bat again.
pause
