@echo off
echo ===============================
echo   Starting Binance API Bot...
echo ===============================

:: Change directory to bot folder
E:
cd "E:\Nayeem\Binance API Bot"

:: Activate bot
python worker.py

echo.
echo Bot stopped. Press any key to exit.
pause >nul
