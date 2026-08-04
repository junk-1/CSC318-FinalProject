@echo off
rem Launches BotVault using the Python environment that has customtkinter + lmdb installed.
cd /d "%~dp0"
"C:\Users\Pivital\miniconda3\python.exe" frontend\App.py

if errorlevel 1 (
    echo.
    echo BotVault exited with an error ^(see above^).
    pause
)
