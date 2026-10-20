@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -c "import tkinter" >nul 2>nul
  if errorlevel 1 (
    echo Python was found, but Tkinter is unavailable.
    echo Install Python from python.org and ensure Tcl/Tk support is selected.
    pause
    exit /b 1
  )
  py -3 -m flashtransfer
  goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
  python -c "import tkinter" >nul 2>nul
  if errorlevel 1 (
    echo Python was found, but Tkinter is unavailable.
    echo Install Python from python.org and ensure Tcl/Tk support is selected.
    pause
    exit /b 1
  )
  python -m flashtransfer
  goto :end
)

echo Python 3 was not found. Install Python 3.10 or newer from python.org,
echo ensure Tcl/Tk support is selected, then run this file again.
pause
exit /b 1

:end
if errorlevel 1 pause
