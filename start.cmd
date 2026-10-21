@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "LONGVIDEO_PYTHON=%~dp0..\..\work\longvideo-venv\Scripts\python.exe"
if not exist "%LONGVIDEO_PYTHON%" (
  echo Python environment not found. Please keep the work folder with this project.
  pause
  exit /b 1
)
"%LONGVIDEO_PYTHON%" -X utf8 "%~dp0desktop_start.py"
if errorlevel 1 pause
