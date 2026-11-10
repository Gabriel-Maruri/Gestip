@echo off
title Gestip
cd /d "%~dp0"
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:5000"
python app.py
pause
