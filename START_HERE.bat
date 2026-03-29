@echo off
title GammaEdge SPX Centroid v6
color 0A

echo.
echo  === GammaEdge SPX Centroid  v6  Tradier ===
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install from https://python.org
    echo  Then re-run this file.
    pause & exit /b
)

echo  Installing requests if needed...
pip install requests --quiet 2>nul

echo  Starting...
echo.

python "%~dp0spx_centroid.py"

echo.
echo  ======================================
echo  Server stopped. See error above.
echo  ======================================
pause
