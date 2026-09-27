@echo off
setlocal

REM Packages an existing dist\Likte.exe as a native Windows installer.
if not exist "dist\Likte.exe" (
    echo ERROR: dist\Likte.exe was not found.
    echo Build the application first with build_windows.bat.
    exit /b 1
)

set "ISCC_PATH="
for %%I in (ISCC.exe) do set "ISCC_PATH=%%~$PATH:I"

if not defined ISCC_PATH if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
)

if not defined ISCC_PATH if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
)

if not defined ISCC_PATH (
    echo ERROR: Inno Setup 6 was not found.
    echo Install it from https://jrsoftware.org/isdl.php, then run this file again.
    exit /b 1
)

"%ISCC_PATH%" "installer\Likte.iss"
if errorlevel 1 (
    echo ERROR: Installer build failed.
    exit /b 1
)

echo.
echo Installer created: dist\installer\Likte-Setup-1.0.0.exe
