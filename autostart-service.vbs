' Hidden logon launcher for the market dashboard background service.
' Started by the shortcut that tools/install_autostart.ps1 puts into the
' user's Startup folder. Keep this file pure ASCII: WScript reads .vbs as
' ANSI unless it carries a BOM, so non-ASCII text here would be mangled.
'
' Optional first argument = delay in milliseconds (default 20000). Handy when
' testing the chain by hand; the shortcut passes nothing.
Option Explicit

Dim shell, fso, root, bat, delay, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
bat = root & "\service_autostart.bat"

If Not fso.FileExists(bat) Then
  WScript.Quit 1
End If

delay = 20000
If WScript.Arguments.Count > 0 Then
  If IsNumeric(WScript.Arguments(0)) Then delay = CLng(WScript.Arguments(0))
End If

' Give the network stack, VPN client and system proxy time to settle before
' the first poll. Without this every feed fails once at logon and the UI
' starts life with cold caches.
If delay > 0 Then
  WScript.Sleep delay
End If

shell.CurrentDirectory = root

' Call through cmd.exe on purpose: launching the .bat by association is
' unreliable under some shell-association policies, and it silently does
' nothing there. 0 = hidden window, False = do not wait.
command = "cmd /c """ & bat & """"
shell.Run command, 0, False
