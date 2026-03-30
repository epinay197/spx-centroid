'' GammaEdge SPX Centroid — silent background launcher
'' Runs spx_centroid.py with no console window at startup

Dim objShell, strDir
Set objShell = CreateObject("WScript.Shell")
strDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
objShell.Run "python """ & strDir & "spx_centroid.py""", 0, False
Set objShell = Nothing
