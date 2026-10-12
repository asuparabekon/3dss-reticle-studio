@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import PySide6.QtWidgets, numpy, PIL, shapely" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements-gui.txt
    if errorlevel 1 goto failed
)
if not exist ".preview\reticle_preview.exe" (
    echo The native preview must be built with build_native.py first.
    echo Authors can use the standalone MSDFReticleStudio package without Python or Visual Studio.
    goto failed
)
start "" /b ".venv\Scripts\pythonw.exe" "%~dp0author_reticle.py" %*
exit /b 0
:failed
pause
exit /b 1
