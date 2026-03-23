@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m uvicorn dcs_lookup.server.main:app --host 192.168.1.178 --port 4328
pause
