@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
py -3.11 -m su_print_counters.diagnostics
set "CRC_DIAGNOSTIC_EXIT_CODE=%errorlevel%"
if not "%CRC_DIAGNOSTIC_EXIT_CODE%"=="0" (
    echo Diagnostica non completata. Invia una foto della finestra per assistenza.
    pause
)
exit /b %CRC_DIAGNOSTIC_EXIT_CODE%
