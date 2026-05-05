Dim WshShell, fso, scriptDir, windowsDir, scriptPath
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
windowsDir = fso.GetParentFolderName(scriptDir)
scriptPath = windowsDir & "\start_dispatch_listener.ps1"

Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & scriptPath & """", 0, False
