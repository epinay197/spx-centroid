@echo off
chcp 65001 >/dev/null

:: Skip launch if US markets are closed today (weekend or holiday)
powershell -NoProfile -Command ^
  "$et=[System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow,[System.TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')).Date;" ^
  "if($et.DayOfWeek-eq'Saturday'-or$et.DayOfWeek-eq'Sunday'){exit 1};" ^
  "$h=@('2025-01-01','2025-01-20','2025-02-17','2025-04-18','2025-05-26','2025-06-19','2025-07-04','2025-09-01','2025-11-27','2025-12-25','2026-01-01','2026-01-19','2026-02-16','2026-04-03','2026-05-25','2026-06-19','2026-07-03','2026-09-07','2026-11-26','2026-12-25','2027-01-01','2027-01-18','2027-02-15','2027-03-26','2027-05-31','2027-06-18','2027-07-05','2027-09-06','2027-11-25','2027-12-24');" ^
  "if($h-contains$et.ToString('yyyy-MM-dd')){exit 1};exit 0"
if errorlevel 1 (
    echo US markets closed today — skipping SPX Centroid launch.
    exit /b 0
)

:: Kill any existing centroid on port 8765
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8765" ^| findstr "LISTENING"') do taskkill /F /PID %%a >/dev/null 2>&1
timeout /t 1 /nobreak >/dev/null

cd /d "C:\Users\Wko\Desktop\spx_centroid"
python spx_centroid.py
