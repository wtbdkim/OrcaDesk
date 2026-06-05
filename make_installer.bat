@echo off
REM ============================================================
REM  ORCAdesk - one-click installer builder
REM
REM  Double-click this to go from source to a finished
REM  installer_output\ORCAdesk-Setup.exe in one step.
REM
REM  It does:
REM    1. install Python deps + PyInstaller
REM    2. build the app  (dist\ORCAdesk\)
REM    3. find Inno Setup and compile installer.iss
REM
REM  PREREQUISITES (one-time):
REM    - Python installed and on PATH
REM    - Inno Setup installed (https://jrsoftware.org/isdl.php)
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo  ORCAdesk - building installer
echo ============================================================
echo.

REM ---- 0. Python present? ----
echo [0/3] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python was not found.
    echo Install Python from https://www.python.org/ and make sure
    echo "Add Python to PATH" is checked during installation.
    echo.
    pause
    exit /b 1
)

REM ---- 1. dependencies ----
echo [1/3] Installing dependencies (this may take a minute)...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: failed to install PyQt6 dependencies.
    pause
    exit /b 1
)
python -m pip install pyinstaller >nul 2>&1

REM ---- 2. build the app ----
echo [2/3] Building the application with PyInstaller...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
python -m PyInstaller build.spec --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed. See the messages above.
    pause
    exit /b 1
)
if not exist "dist\ORCAdesk\ORCAdesk.exe" (
    echo.
    echo ERROR: build did not produce dist\ORCAdesk\ORCAdesk.exe
    pause
    exit /b 1
)
echo       Build OK: dist\ORCAdesk\ORCAdesk.exe

REM ---- 3. find Inno Setup compiler (ISCC.exe) ----
echo [3/3] Locating Inno Setup...
set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "C:\Program Files (x86)\Inno Setup 5\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 5\ISCC.exe"
if not defined ISCC if exist "C:\Program Files\Inno Setup 5\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 5\ISCC.exe"

REM also try PATH
if not defined ISCC (
    for %%I in (ISCC.exe) do if not "%%~$PATH:I"=="" set "ISCC=%%~$PATH:I"
)

if not defined ISCC (
    echo.
    echo ============================================================
    echo  Inno Setup was not found.
    echo.
    echo  The app itself built fine (dist\ORCAdesk\), but to make
    echo  the installer you need Inno Setup:
    echo    1. Download + install from https://jrsoftware.org/isdl.php
    echo    2. Run this script again.
    echo.
    echo  (Or just zip the dist\ORCAdesk\ folder and share that
    echo   instead of an installer.)
    echo ============================================================
    echo.
    pause
    exit /b 1
)

echo       Found: !ISCC!
echo       Compiling installer...
"!ISCC!" installer.iss
if errorlevel 1 (
    echo.
    echo ERROR: Inno Setup compilation failed. See messages above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  DONE!
echo.
echo  Installer created:
echo    installer_output\ORCAdesk-Setup.exe
echo.
echo  Give that single file to anyone. They double-click it to
echo  install ORCAdesk. (They still need ORCA installed and
echo  set its path in Settings on first launch.)
echo ============================================================
echo.
pause
endlocal
