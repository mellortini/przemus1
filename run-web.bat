@echo off
title Przemuś - Web
cd /d "%~dp0src"
echo.
echo ========================================
echo   Przemuś - Web Interface
echo   Otworz: http://localhost:5000
echo ========================================
echo.
python web.py
pause
