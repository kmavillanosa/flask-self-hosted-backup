@echo off
REM Stop Flask app running in background
echo Stopping Flask backup receiver...
for /f "tokens=2" %%a in ('tasklist ^| findstr /i "pythonw.exe python.exe" ^| findstr /i "receiver.py"') do (
    echo Killing process %%a
    taskkill /PID %%a /F
)
REM Also try to kill by port if process name doesn't match
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5001') do (
    echo Killing process using port 5001: %%a
    taskkill /PID %%a /F 2>nul
)
echo Done.
pause

