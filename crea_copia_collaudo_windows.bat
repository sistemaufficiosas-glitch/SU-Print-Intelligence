@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
py -3.11 -m su_print_counters.snapshot
set "CRC_COPY_EXIT_CODE=%errorlevel%"
if not "%CRC_COPY_EXIT_CODE%"=="0" (
    echo Impossibile avviare la preparazione della copia. Invia una foto di questa finestra.
    pause
)
exit /b %CRC_COPY_EXIT_CODE%
