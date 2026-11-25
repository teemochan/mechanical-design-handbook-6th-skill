@echo off
chcp 65001 >nul
set "KB_DIR=%~dp0"
if exist "%KB_DIR%.venv\Scripts\python.exe" (
    "%KB_DIR%.venv\Scripts\python.exe" "%KB_DIR%scripts\handbook_cli.py" %*
    exit /b %ERRORLEVEL%
)
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 "%KB_DIR%scripts\handbook_cli.py" %*
    exit /b %ERRORLEVEL%
)
python "%KB_DIR%scripts\handbook_cli.py" %*
