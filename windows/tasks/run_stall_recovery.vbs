Dim fso, scriptDir, scriptPath, wmi, startup, proc, pid
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = scriptDir & "\run_stall_recovery.ps1"

Set wmi = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
Set startup = wmi.Get("Win32_ProcessStartup").SpawnInstance_
startup.CreateFlags = 134217728

Set proc = wmi.Get("Win32_Process")
proc.Create "powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & scriptPath & """", Null, startup, pid
