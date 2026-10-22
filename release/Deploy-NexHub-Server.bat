@echo off
setlocal
cd /d "%~dp0"

if not exist "NexHub-Server-Bundle.zip" (
  echo [ERROR] NexHub-Server-Bundle.zip not found next to this script.
  pause
  exit /b 1
)
if not exist "install_server.sh" (
  echo [ERROR] install_server.sh not found next to this script.
  pause
  exit /b 1
)
where scp >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows OpenSSH scp was not found.
  echo Install "OpenSSH Client" from Windows Optional Features and try again.
  pause
  exit /b 1
)
where ssh >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows OpenSSH ssh was not found.
  echo Install "OpenSSH Client" from Windows Optional Features and try again.
  pause
  exit /b 1
)

set "SERVER_HOST=47.108.130.177"
set /p SERVER_HOST=Server IP [%SERVER_HOST%]: 
if "%SERVER_HOST%"=="" set "SERVER_HOST=47.108.130.177"

set "SERVER_USER=root"
set /p SERVER_USER=SSH user [%SERVER_USER%]: 
if "%SERVER_USER%"=="" set "SERVER_USER=root"

set "SERVER_PORT=22"
set /p SERVER_PORT=SSH port [%SERVER_PORT%]: 
if "%SERVER_PORT%"=="" set "SERVER_PORT=22"

echo.
echo Uploading server files to %SERVER_USER%@%SERVER_HOST%...
scp -P %SERVER_PORT% "NexHub-Server-Bundle.zip" "%SERVER_USER%@%SERVER_HOST%:/tmp/nexhub-server-bundle.zip"
if errorlevel 1 goto :failed
scp -P %SERVER_PORT% "install_server.sh" "%SERVER_USER%@%SERVER_HOST%:/tmp/nexhub-install.sh"
if errorlevel 1 goto :failed

echo.
echo Installing and restarting NexHub server...
ssh -p %SERVER_PORT% "%SERVER_USER%@%SERVER_HOST%" "if [ \"$(id -u)\" = \"0\" ]; then bash /tmp/nexhub-install.sh; else sudo bash /tmp/nexhub-install.sh; fi"
if errorlevel 1 goto :failed

echo.
echo [OK] Server deployment completed.
pause
exit /b 0

:failed
echo.
echo [FAILED] Deployment did not complete. Check the output above.
pause
exit /b 1
