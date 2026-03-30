@echo off
title GammaEdge SPX Centroid — Install Autostart
color 0A

echo.
echo  === GammaEdge SPX Centroid — Auto-Start Installer ===
echo.

set "VBS=%~dp0run_silent.vbs"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "DST=%STARTUP%\GammaEdge_SPX_Centroid.vbs"

echo  Installing to Windows Startup folder...
echo  (no admin required — runs at every login)
echo.

copy /y "%VBS%" "%DST%" >nul

if exist "%DST%" (
    echo  Done! Server will start automatically on every login.
    echo.
    echo  Location: %DST%
    echo  Dashboard: http://localhost:8765
    echo.
    echo  To remove autostart, run: remove_autostart.bat
) else (
    echo  ERROR: Could not copy file. Check that the path exists:
    echo  %STARTUP%
)

echo.
pause
