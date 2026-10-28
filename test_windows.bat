@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
py -3.11 -m unittest discover -s tests -v
exit /b %errorlevel%
