@echo off
:: Run this as Administrator
:: Make sure NSSM is installed and in PATH

SET SERVICE_NAME=DAGBot
SET BOT_DIR=%~dp0
SET PYTHON=C:\Users\mrrut\AppData\Local\Programs\Python\Python311\python.exe

mkdir "%BOT_DIR%logs" 2>nul

echo Installing DAGBot as Windows Service...

nssm install %SERVICE_NAME% "%PYTHON%" "%BOT_DIR%bot.py"
nssm set %SERVICE_NAME% AppDirectory "%BOT_DIR%"
nssm set %SERVICE_NAME% AppStdout "%BOT_DIR%logs\dagbot.log"
nssm set %SERVICE_NAME% AppStderr "%BOT_DIR%logs\dagbot_error.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateBytes 10485760
nssm set %SERVICE_NAME% Start SERVICE_AUTO_START
nssm set %SERVICE_NAME% DisplayName "DAG Club Documentation Bot"
nssm set %SERVICE_NAME% Description "Telegram bot for DAG Club event documentation automation"

nssm start %SERVICE_NAME%

echo.
echo DAGBot service installed and started!
echo Use 'nssm stop DAGBot' to stop it
echo Use 'nssm restart DAGBot' to restart it
pause
