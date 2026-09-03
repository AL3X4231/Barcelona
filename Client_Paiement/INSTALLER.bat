@echo off
setlocal
cd /d "%~dp0"
title Installation Sevilla FC

echo Installation des dependances Python...
python -m pip install camoufox[geoip] requests

echo.
echo Telechargement du navigateur Camoufox...
python -m camoufox fetch

echo.
echo Installation terminee avec succes.
pause
endlocal
