@echo off
setlocal
cd /d "%~dp0"

if not exist "nexhub\resources\npc\npc.exe" (
    echo [ERROR] Missing nexhub\resources\npc\npc.exe
    echo Put the complete npc folder into client\nexhub\resources\npc first.
    pause
    exit /b 1
)

python -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

python -m PyInstaller ^
  --noconfirm --clean --onedir --windowed ^
  --name NexHub ^
  --icon "nexhub\resources\nexhub.ico" ^
  --add-data "nexhub\resources;nexhub\resources" ^
  --collect-all qfluentwidgets ^
  --hidden-import nexhub.plugin_host ^
  --hidden-import nexhub.services.memory_optimizer ^
  --hidden-import nexhub.npc_admin ^
  --hidden-import win32crypt ^
  --hidden-import yaml ^
  main.py

if errorlevel 1 exit /b 1
echo.
echo Built folder: %CD%\dist\NexHub\NexHub.exe
echo Use tools\build_client_release.ps1 to create the portable zip and Inno installer.
pause
