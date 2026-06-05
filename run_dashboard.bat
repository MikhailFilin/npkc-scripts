@echo off

:: Script directory (absolute path, works from Task Scheduler)
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

:: Log file
set LOGFILE=%SCRIPT_DIR%scheduler.log
echo [%DATE% %TIME%] Start: %1 >> "%LOGFILE%"

:: Run via py launcher (available globally on Windows)
py -3 "%SCRIPT_DIR%runner.py" "%1"

echo [%DATE% %TIME%] Done: %1, exit=%ERRORLEVEL% >> "%LOGFILE%"
