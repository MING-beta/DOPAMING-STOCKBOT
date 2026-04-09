@echo off
echo ====================================================
echo  Dopaming Stock Bot - 32Bit Execution Launcher
echo ====================================================
echo.
echo Starting the bot using 32-bit venv...
echo.

if not exist "venv32\Scripts\python.exe" (
    echo [ERROR] venv32 is missing or corrupted.
    pause
    exit /b
)

.\venv32\Scripts\python.exe main.py

echo.
echo Program execution finished.
pause
