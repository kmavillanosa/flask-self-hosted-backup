@echo off
REM Start Flask app in background
echo Starting Flask backup receiver in background...
start /B pythonw receiver.py
if %ERRORLEVEL% EQU 0 (
    echo Flask app started successfully in background.
    echo Check the process with: tasklist | findstr pythonw
    echo To stop it, use: stop_background.bat
) else (
    echo Failed to start Flask app. Trying with regular python...
    start /B python receiver.py
)
pause

