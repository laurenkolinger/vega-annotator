' Open VEGA Annotator with no console window. The shortcut on the desktop runs this file.
Set Fso = CreateObject("Scripting.FileSystemObject")
Set Shell = CreateObject("WScript.Shell")
AppDir = Fso.GetParentFolderName(WScript.ScriptFullName)
Python = Fso.BuildPath(Fso.GetParentFolderName(AppDir), "runtime\Scripts\pythonw.exe")
Shell.CurrentDirectory = AppDir
Shell.Environment("PROCESS")("PYTHONUTF8") = "1"
Shell.Run """" & Python & """ -m vega_annotator.launcher", 0, False
