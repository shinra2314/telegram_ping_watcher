' Windowless launcher for the Pulse Desk dashboard.
' Runs start_dashboard.ps1 with a hidden window (style 0) so no terminal
' ever flashes at logon. Used as the Task Scheduler / Startup action.
Dim sh, fso, here, ps1
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = here & "\start_dashboard.ps1"
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """", 0, False
