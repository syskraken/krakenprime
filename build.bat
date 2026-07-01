@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title KRAKEN PRIME - Build EXE
color 0E

echo.
echo  ============================================================
echo    KRAKEN PRIME
echo  ============================================================
echo.
echo  Compiles the program into a single executable file
echo  KrakenPrime.exe. No Python needed on end-user PCs.
echo.
echo.
echo  Press any key to start the build...
pause >nul

:: ── Find Python ───────────────────────────────────────────────
set PYTHON_CMD=
python --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_CMD=python & goto :found_python )

set PYTHON_LOCAL=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
if exist "%PYTHON_LOCAL%" ( set PYTHON_CMD="%PYTHON_LOCAL%" & goto :found_python )

set PYTHON_GLOBAL=C:\Python311\python.exe
if exist "%PYTHON_GLOBAL%" ( set PYTHON_CMD="%PYTHON_GLOBAL%" & goto :found_python )

echo  [!] Python not found. Run setup.bat first.
pause & exit /b 1

:found_python
echo  [OK] Python found: %PYTHON_CMD%

:: ── Install / upgrade PyInstaller ────────────────────────────
echo.
echo  [1/4] Installing PyInstaller...
%PYTHON_CMD% -m pip install pyinstaller --upgrade --quiet
if errorlevel 1 (
    echo  [!] Could not install PyInstaller.
    pause & exit /b 1
)
echo  [OK] PyInstaller ready.

:: ── Locate customtkinter data folder ─────────────────────────
echo.
echo  [2/4] Locating customtkinter assets...
for /f "delims=" %%i in ('%PYTHON_CMD% -c "import customtkinter, os; print(os.path.dirname(customtkinter.__file__))"') do (
    set CTK_PATH=%%i
)
if not defined CTK_PATH (
    echo  [!] Could not locate customtkinter. Run: pip install customtkinter
    pause & exit /b 1
)
echo  [OK] customtkinter: !CTK_PATH!


for /f "delims=" %%i in ('%PYTHON_CMD% -c "import certifi; print(certifi.where())"') do (
    set CERTIFI_PATH=%%i
)
if not defined CERTIFI_PATH (
    echo  [!] Could not locate certifi. Run: pip install certifi
    pause & exit /b 1
)
echo  [OK] certifi: !CERTIFI_PATH!

:: ── Convert icon.png to icon.ico if needed ───────────────────
echo.
echo  [3/4] Preparing icon...
set ICON_ARG=
if exist "%~dp0icon.ico" (
    set ICON_ARG=--icon "%~dp0icon.ico"
    echo  [OK] icon.ico found.
) else if exist "%~dp0icon.png" (
    echo  [~] Converting icon.png to icon.ico...
    %PYTHON_CMD% -c "from PIL import Image; img=Image.open('icon.png'); img.save('icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])"
    if exist "%~dp0icon.ico" (
        set ICON_ARG=--icon "%~dp0icon.ico"
        echo  [OK] icon.ico created from icon.png.
    ) else (
        echo  [!] Icon conversion failed - building without icon.
    )
) else (
    echo  [~] No icon file found - building without icon.
)

:: ── Copy ADB alongside the built exe ──────────────────────────
echo.
echo  [~] Copying ADB tools next to the exe...
copy "%~dp0adb.exe"          "%~dp0dist\adb.exe"          >nul 2>&1
copy "%~dp0AdbWinApi.dll"    "%~dp0dist\AdbWinApi.dll"    >nul 2>&1
copy "%~dp0AdbWinUsbApi.dll" "%~dp0dist\AdbWinUsbApi.dll" >nul 2>&1
if exist "%~dp0dist\adb.exe" (
    echo  [OK] adb.exe copied to dist\
) else (
    echo  [!] adb.exe not found in project folder — run setup.bat first.
)

:: ── Build the EXE ─────────────────────────────────────────────
echo.
echo  [4/4] Building EXE (this takes 2-5 minutes)...
echo.

%PYTHON_CMD% -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name "KrakenPrime" ^
    %ICON_ARG% ^
    --add-data "%~dp0deploy_overlay.py;." ^
    --add-data "%~dp0app.py;." ^
    --add-data "%~dp0icon.png;." ^
    --add-data "%~dp0templates;templates" ^
    --add-data "!CTK_PATH!;customtkinter" ^
    --add-data "!CERTIFI_PATH!;certifi" ^
    --add-binary "%~dp0adb.exe;." ^
    --add-binary "%~dp0AdbWinApi.dll;." ^
    --add-binary "%~dp0AdbWinUsbApi.dll;." ^
    --hidden-import requests ^
    --hidden-import certifi ^
    --hidden-import urllib3 ^
    --hidden-import customtkinter ^
    --hidden-import PIL._tkinter_finder ^
    --hidden-import cv2 ^
    --hidden-import pytesseract ^
    --hidden-import numpy ^
    --hidden-import winreg ^
    gui_app.py

if errorlevel 1 (
    echo.
    echo  ============================================================
    echo    [!] BUILD FAILED. See errors above.
    echo  ============================================================
    pause & exit /b 1
)

:: ── Confirm output exists ─────────────────────────────────────
if not exist "%~dp0dist\KrakenPrime.exe" (
    echo  [!] dist\KrakenPrime.exe not found after build. Check errors above.
    pause & exit /b 1
)

echo.
echo  ============================================================
echo    BUILD COMPLETE!
echo  ============================================================
echo.
echo  EXE location:
echo    %~dp0dist\KrakenPrime.exe
echo.
echo    NOTE: templates\ folder is now bundled INSIDE the exe.
echo    End-users do NOT need a separate templates\ folder.
echo.
pause