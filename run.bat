@echo off
cd /d "%~dp0"

REM Check if virtual environment exists
if exist .venv goto :run_app

echo Virtual environment (.venv) not found.
echo Please run the installation steps in README.md first.
pause
exit /b 1

:run_app
REM Start the application in windowless mode
start "" ".venv\Scripts\pythonw.exe" -m src.main
exit
