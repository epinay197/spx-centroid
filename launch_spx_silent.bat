@echo off
REM Silent launcher for SPX Centroid
REM Runs hidden in background on port 8052

cd /d "%~dp0"
start "" pythonw.exe spx_centroid.py
exit
