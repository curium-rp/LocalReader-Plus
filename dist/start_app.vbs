Set WshShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strPath = Wscript.ScriptFullName
Set objFile = objFSO.GetFile(strPath)
strFolder = objFSO.GetParentFolderName(objFile)

WshShell.CurrentDirectory = strFolder

' Run hidden (0), output all logs/errors to crash.log, and WAIT (True) for the app to close
intReturn = WshShell.Run("cmd /c uv run main.py > crash.log 2>&1", 0, True)

' If Python crashes or returns an error code
If intReturn <> 0 Then
    ' Open a visible command prompt (1) showing the error log and pausing
    WshShell.Run "cmd /c type crash.log & color 0C & echo. & echo [APP CRASHED - EXIT CODE " & intReturn & "] Press any key to close this window... & pause>nul", 1, False
Else
    ' Clean up the log file if the application closed normally
    If objFSO.FileExists("crash.log") Then
        objFSO.DeleteFile("crash.log")
    End If
End If

Set WshShell = Nothing
Set objFSO = Nothing