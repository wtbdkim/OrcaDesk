@echo off
REM ============================================================
REM  ORCAdesk - Windows build script
REM  Produces dist\ORCAdesk\ORCAdesk.exe (+ runtime folder)
REM ============================================================

setlocal

echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    exit /b 1
)

echo [2/4] Installing / updating dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 (
    echo ERROR: dependency install failed.
    exit /b 1
)

echo [3/4] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [4/4] Building with PyInstaller (this takes a few minutes)...
python -m PyInstaller build.spec --noconfirm
if errorlevel 1 (
    echo ERROR: build failed.
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete.
echo  App folder:  dist\ORCAdesk\
echo  Run:         dist\ORCAdesk\ORCAdesk.exe
echo.
echo  To share with a friend: zip the entire dist\ORCAdesk\
echo  folder and send it. They unzip and run ORCAdesk.exe.
echo  (They still need ORCA installed; set its path in Settings.)
echo ============================================================

endlocal
