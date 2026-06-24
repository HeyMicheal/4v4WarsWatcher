' OCRワーカーをコンソール非表示で起動する（自動起動向け）。
' ログは worker/worker.log に残る。
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
' pythonw はコンソールを出さずに実行する
sh.Run "pythonw worker.py", 0, False
