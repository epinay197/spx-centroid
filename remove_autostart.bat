@echo off
title GammaEdge SPX Centroid — Remove Autostart
color 0C

echo.
echo  === GammaEdge SPX Centroid — Remove Auto-Start ===
echo.

set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GammaEdge_SPX_Centroid.vbs"

if exist "%DST%" (
    del /f /q "%DST%"
    echo  Auto-start removed successfully.
) else (
    echo  Not installed — nothing to remove.
)

echo.
pause
