' Windowless launcher for pulse_watchdog.ps1.
' powershell.exe -WindowStyle Hidden still flashes a console when Task
' Scheduler runs it in the interactive session; wscript.exe never spawns a
' console at all, so the 5-minute watchdog tick is truly invisible.
' Third sh.Run argument is True (wait): the wscript process stays alive for
' the whole watchdog run, so the task's MultipleInstances=IgnoreNew setting
' keeps guaranteeing a single concurrent instance (pulse_watchdog.ps1 relies
' on that as the sole writer of logs\watchdog.log).
Dim sh, fso, here, ps1
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = here & "\pulse_watchdog.ps1"
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """", 0, True
