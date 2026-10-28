@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
py -3.11 -m unittest discover -s tests -v
set "CRC_TEST_EXIT_CODE=%errorlevel%"
echo.
if "%CRC_TEST_EXIT_CODE%"=="0" (
    echo COLLAUDO SUPERATO.
) else (
    echo COLLAUDO NON SUPERATO. Invia una foto di questa finestra per assistenza.
)
pause
exit /b %CRC_TEST_EXIT_CODE%
