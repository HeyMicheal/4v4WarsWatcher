@echo off
REM OCRワーカーを起動する（コンソール表示あり）。
REM このバッチのある worker フォルダを作業ディレクトリにする。
cd /d "%~dp0"
python worker.py
pause
