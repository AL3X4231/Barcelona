@echo off
setlocal
cd /d "%~dp0"
title Sevilla FC - Panier

if exist "..\venv\Scripts\python.exe" (
    "..\venv\Scripts\python.exe" assistant_gui.py
    goto end
)

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" assistant_gui.py
    goto end
)

python assistant_gui.py

:end
if errorlevel 1 pause
endlocal
