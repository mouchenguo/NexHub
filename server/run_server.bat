@echo off
setlocal
cd /d "%~dp0"
python run.py --host 0.0.0.0 --port 8000
if errorlevel 1 pause
