' run_heartbeat_miru_flask.vbs -- Window-subsystem launcher for the
' MiruFlaskHeartbeat scheduled task. wscript.exe runs window-subsystem
' (no console) and SW_HIDE on the powershell child guarantees no popup,
' avoiding the Win11 24H2 flash from direct powershell.exe task actions.
'
' Task action should be:
'   Execute:  wscript.exe
'   Argument: "D:\dev\miru\windows\tasks\run_heartbeat_miru_flask.vbs"

Dim WshShell, fso, scriptDir, scriptPath
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = scriptDir & "\heartbeat_miru_flask_task.ps1"

Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & scriptPath & """", 0, False
