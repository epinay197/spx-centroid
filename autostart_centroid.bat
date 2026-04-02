@echo off
chcp 65001 >/dev/null

:: Kill any existing centroid on port 8765
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8765" ^| findstr "LISTENING"') do taskkill /F /PID %%a >/dev/null 2>&1
timeout /t 1 /nobreak >/dev/null

cd /d "C:\Users\Wko\Desktop\spx_centroid"
python spx_centroid.py
