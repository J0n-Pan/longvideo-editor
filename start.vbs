Option Explicit
Dim fs, shell, folder, pythonw, command
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
pythonw = fs.GetAbsolutePathName(fs.BuildPath(folder, "..\..\work\longvideo-venv\Scripts\pythonw.exe"))
If Not fs.FileExists(pythonw) Then
    MsgBox "Python runtime not found. Please keep the workspace folder layout.", 16, "Long Video Editor"
    WScript.Quit 1
End If
command = Chr(34) & pythonw & Chr(34) & " -X utf8 " & Chr(34) & fs.BuildPath(folder, "desktop_start.py") & Chr(34)
shell.Run command, 0, False
