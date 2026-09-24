' Windowless launcher for pulse_watchdog.ps1.
' powershell.exe -WindowStyle Hidden still flashes a console when Task
' Scheduler runs it in the interactive session; wscript.exe never spawns a
' console at all, so the 5-minute watchdog tick is truly invisible.
' Third sh.Run argument is True (wait): the wscript process stays alive for
' the whole watchdog run, so the task's MultipleInstances=IgnoreNew setting
' keeps guaranteeing a single concurrent instance (pulse_watchdog.ps1 relies
' on that as the sole writer of logs\watchdog.log).
Dim sh, fso, here, ps1, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = here & "\pulse_watchdog.ps1"
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """"

' sh.Run (ShellExecute) can throw Permission Denied on a transient race - AV
' on-access-scanning powershell.exe, or general contention right after a
' reboot/login. Unhandled, that pops a blocking "Windows Script Host" dialog,
' which defeats "hidden" and can wedge every later tick behind
' MultipleInstances=IgnoreNew until someone is there to click OK. Retry once,
' then give up quietly: the next scheduled tick tries again in a few minutes.
On Error Resume Next
sh.Run cmd, 0, True
If Err.Number <> 0 Then
    Err.Clear
    WScript.Sleep 3000
    sh.Run cmd, 0, True
End If
