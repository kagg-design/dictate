@echo off
cd /d "%~dp0"

:: Check if virtual environment exists
if not exist .venv (
    echo Virtual environment (.venv) not found.
    echo Please run the installation steps in README.md first.
    pause
    exit /b 1
)

:: Start the application in windowless mode
start "" ".venv\Scripts\pythonw.exe" -m src.main
exit
