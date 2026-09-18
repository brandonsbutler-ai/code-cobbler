@echo off
REM CodeCobbler -- Windows launcher.
REM
REM Double-click with nothing selected: opens the shelf of maps you have made.
REM Drag a project folder onto this file: maps that folder and opens the map.
REM
REM To put it on the desktop: right-click this file -> Send to -> Desktop
REM (create shortcut). The shortcut inherits drag-and-drop.
REM
REM All behaviour lives in cobblerpy\launch.py; this only locates Python and
REM the checkout, because the package is not pip-installed on this machine.
setlocal
set "REPO=W:\LinuxSideClaudeProjects\CP\resume\cobblerpy"

if not exist "%REPO%\cobblerpy\launch.py" (
  echo CodeCobbler: cannot find the checkout at %REPO%
  echo Edit REPO at the top of this file to point at it.
  pause
  exit /b 1
)

REM py.exe is the Windows Python launcher and is the right first choice; fall
REM back to python on PATH. Neither present is a real answer, not a crash.
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo CodeCobbler needs Python 3.9 or newer on this machine.
  echo Install it from python.org or the Microsoft Store, then run this again.
  pause
  exit /b 1
)

set "PYTHONPATH=%REPO%;%PYTHONPATH%"
if "%~1"=="" (
  %PY% -m cobblerpy.launch --shelf
) else (
  %PY% -m cobblerpy.launch %*
)
if errorlevel 1 pause
endlocal
