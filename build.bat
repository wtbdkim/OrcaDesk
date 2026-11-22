@echo off
REM ============================================================
REM  ORCAdesk - Windows build script
REM  Produces dist\ORCAdesk\ORCAdesk.exe (+ runtime folder)
REM
REM  The build runs in a clean virtual environment, .venv-build\
REM  (git-ignored), recreated on every run from the pinned
REM  requirements-build.txt. PyInstaller bundles whatever it can
REM  import, so building from a global site-packages would ship
REM  the build machine's other packages, and THIRD_PARTY_NOTICES.txt
REM  would no longer describe the bundle.
REM ============================================================

setlocal

REM The interpreter the notices name (THIRD_PARTY_NOTICES.txt).
set PYVER=3.12.10

echo [1/5] Checking Python...
set PY=
py -3.12 --version >nul 2>&1
if not errorlevel 1 set PY=py -3.12
if not defined PY (
    python --version >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python 3.12 not found ^(py -3.12 or python on PATH^).
        exit /b 1
    )
    set PY=python
)
for /f "tokens=2" %%v in ('%PY% --version') do set FOUNDVER=%%v
echo        using %PY% ^(Python %FOUNDVER%^)
if not "%FOUNDVER%"=="%PYVER%" (
    echo WARNING: THIRD_PARTY_NOTICES.txt describes Python %PYVER%; this is %FOUNDVER%.
    echo          Update the notices or build with Python %PYVER%.
)

echo [2/5] Creating the clean build environment .venv-build\ ...
if exist .venv-build rmdir /s /q .venv-build
%PY% -m venv .venv-build
if errorlevel 1 (
    echo ERROR: could not create .venv-build.
    exit /b 1
)
set VPY=.venv-build\Scripts\python.exe

echo [3/5] Installing the pinned dependencies ^(requirements-build.txt^)...
%VPY% -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 (
    echo ERROR: dependency install failed ^(requirements-build.txt^).
    exit /b 1
)
%VPY% -m pip freeze --all

echo [4/5] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [5/5] Building with PyInstaller (this takes a few minutes)...
%VPY% -m PyInstaller build.spec --noconfirm
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
echo  The folder carries LICENSE-BINARY.txt, THIRD_PARTY_NOTICES.txt
echo  and licenses\ - keep them with it.
echo  Installer: "C:\Program Files\Inno Setup 7\ISCC.exe" installer.iss
echo ============================================================

endlocal
