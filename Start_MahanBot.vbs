' Start_MahanBot.vbs — Silent 1-click launcher for MAHANBOT
Set WshShell = CreateObject("WScript.Shell")
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath
WshShell.Run "cmd /c run_client.bat", 0, False
