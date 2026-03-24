@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m uvicorn dcs_lookup.server.DcsServerMain:app --host 0.0.0.0 --port 4328
pause
