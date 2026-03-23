@echo off
cd /d "%~dp0"
pyinstaller --noconfirm dcs_ui.spec
copy /y DCS.ico dist\DCS.ico
copy /y launch.bat dist\launch.bat
echo.
echo Build complete.
echo Deploy this folder to the target machine:
echo   dist\DcsLookup.exe
echo   dist\DCS.ico
echo   dist\launch.bat
echo   config.json  (edit server_url, place next to the exe)
pause
