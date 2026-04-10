' Silent wrapper for SPX Centroid (no cmd window)
Set WshShell = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
' Kill existing instance on port 8765
WshShell.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -aon ^| findstr "":8765"" ^| findstr ""LISTENING""') do taskkill /F /PID %a >nul 2>&1", 0, True
WScript.Sleep 1000
WshShell.Run "cmd /c cd /d """ & dir & """ && python spx_centroid.py", 0, False
