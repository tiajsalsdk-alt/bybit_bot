@echo off
cd /d "%~dp0"
:loop
python main.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Bot exited with error code %ERRORLEVEL%.
    echo [INFO] Kill-Switch triggered or Critical Error. Check bot.log.
    pause
    exit /b %ERRORLEVEL%
)
goto loop
