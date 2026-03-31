Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptPath = fso.GetParentFolderName(WScript.ScriptFullName) & "\HFDownloader_GUI.pyw"
shell.Run "pyw """ & scriptPath & """", 0, False
