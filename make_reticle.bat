@echo off
rem Drop an SVG reticle drawing on this file, or run: make_reticle.bat my_reticle.svg
rem The first run sets up a private Python environment next to this file (needs Python 3.10+ and internet).
setlocal
set HERE=%~dp0
if "%~1"=="" (
    echo Drop an SVG file on make_reticle.bat, or run: make_reticle.bat my_reticle.svg
    pause
    exit /b 1
)
where py >nul 2>nul && (set PY=py -3) || (
    where python >nul 2>nul && (set PY=python) || (
        echo Python 3 was not found. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
        pause
        exit /b 1
    )
)
if not exist "%HERE%.venv\Scripts\python.exe" (
    echo Setting up the reticle tools, once...
    %PY% -m venv "%HERE%.venv" || goto failed
)
"%HERE%.venv\Scripts\python.exe" -c "import numpy, PIL, shapely" >nul 2>nul
if errorlevel 1 "%HERE%.venv\Scripts\python.exe" -m pip install --quiet -r "%HERE%requirements.txt" || goto failed
"%HERE%.venv\Scripts\python.exe" "%HERE%make_reticle.py" %*
set RESULT=%ERRORLEVEL%
pause
exit /b %RESULT%

:failed
echo Setting up the reticle tools failed; see the messages above.
pause
exit /b 1
