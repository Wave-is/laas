@echo off
cd /d "%~dp0"
if exist "LocalAgentAIStation.exe" (
  start "" "%~dp0LocalAgentAIStation.exe" %*
  exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.pyw" %*
  exit /b
)
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Use the packaged Windows release or follow README.md.
  pause
  exit /b 1
)
start "" pythonw.exe "%~dp0main.pyw" %*
