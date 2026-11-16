@echo off
rem Double-click on Windows to start OD Studio.
rem Needs only Python 3.9 or newer; everything else is in this folder.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%~dp0vendor;%PYTHONPATH%"
where py >nul 2>nul && (py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" && (py -3 -m odstudio gui %* & goto :eof))
where python >nul 2>nul && (python -c "import sys; sys.exit(sys.version_info < (3, 9))" && (python -m odstudio gui %* & goto :eof))
echo Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/ and try again.
pause
