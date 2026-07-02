@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title KRAKEN PRIME - Build EXE
color 0E

echo.
echo  ============================================================
echo    KRAKEN PRIME - BUILD
echo  ============================================================
echo.
echo  Compiles the program into a single executable (KrakenPrime.exe)
echo  and assembles a ready-to-share "release" folder for end users.
echo.
echo  Press any key to start the build...
pause >nul

:: ============================================================
:: [1/5] Pre-flight: all required project files present?
:: ============================================================
echo.
echo  [1/5] Checking project files...
set MISSING=0
for %%F in (gui_app.py app.py deploy_overlay.py telemetry_client.py requirements.txt setup.bat adb.exe AdbWinApi.dll AdbWinUsbApi.dll) do (
    if not exist "%~dp0%%F" (
        echo  [!] Missing: %%F
        set MISSING=1
    )
)
if not exist "%~dp0templates" (
    echo  [!] Missing: templates\ folder
    set MISSING=1
)
if !MISSING! EQU 1 (
    echo.
    echo  [!] Fix the missing files above, then run build.bat again.
    echo      ^(adb.exe and its DLLs are downloaded by setup.bat^)
    pause & exit /b 1
)
echo  [OK] All project files present.

:: ============================================================
:: Find Python 3.11
:: ============================================================
set PYTHON_CMD=
set PYTHON_LOCAL=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
set PYTHON_GLOBAL=C:\Python311\python.exe

if exist "%PYTHON_LOCAL%" ( set "PYTHON_CMD=%PYTHON_LOCAL%" & goto :found_python )
if exist "%PYTHON_GLOBAL%" ( set "PYTHON_CMD=%PYTHON_GLOBAL%" & goto :found_python )
python --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_CMD=python & goto :found_python )

echo  [!] Python not found. Run setup.bat first.
pause & exit /b 1

:found_python
echo  [OK] Python found: %PYTHON_CMD%

:: ============================================================
:: [2/5] PyInstaller + bundled package assets
:: ============================================================
echo.
echo  [2/5] Installing PyInstaller...
"%PYTHON_CMD%" -m pip install pyinstaller --upgrade --quiet
if errorlevel 1 (
    echo  [!] Could not install PyInstaller.
    pause & exit /b 1
)
echo  [OK] PyInstaller ready.

echo.
echo  Locating customtkinter assets...
set CTK_PATH=
del "%TEMP%\kraken_ctk.txt" >nul 2>&1
"%PYTHON_CMD%" -c "import customtkinter, os; open(r'%TEMP%\kraken_ctk.txt','w').write(os.path.dirname(customtkinter.__file__))"
if exist "%TEMP%\kraken_ctk.txt" set /p CTK_PATH=<"%TEMP%\kraken_ctk.txt"
del "%TEMP%\kraken_ctk.txt" >nul 2>&1
if not defined CTK_PATH (
    echo  [!] Could not locate customtkinter. Run setup.bat first.
    pause & exit /b 1
)
echo  [OK] customtkinter: !CTK_PATH!

set CERTIFI_PATH=
del "%TEMP%\kraken_cert.txt" >nul 2>&1
"%PYTHON_CMD%" -c "import certifi; open(r'%TEMP%\kraken_cert.txt','w').write(certifi.where())"
if exist "%TEMP%\kraken_cert.txt" set /p CERTIFI_PATH=<"%TEMP%\kraken_cert.txt"
del "%TEMP%\kraken_cert.txt" >nul 2>&1
if not defined CERTIFI_PATH (
    echo  [!] Could not locate certifi. Run setup.bat first.
    pause & exit /b 1
)
echo  [OK] certifi: !CERTIFI_PATH!

:: ============================================================
:: [3/5] Icon
:: ============================================================
echo.
echo  [3/5] Preparing icon...
set ICON_ARG=
if exist "%~dp0icon.ico" (
    set ICON_ARG=--icon "%~dp0icon.ico"
    echo  [OK] icon.ico found.
) else if exist "%~dp0icon.png" (
    echo  [~] Converting icon.png to icon.ico...
    "%PYTHON_CMD%" -c "from PIL import Image; img=Image.open('icon.png'); img.save('icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])"
    if exist "%~dp0icon.ico" (
        set ICON_ARG=--icon "%~dp0icon.ico"
        echo  [OK] icon.ico created from icon.png.
    ) else (
        echo  [!] Icon conversion failed - building without icon.
    )
) else (
    echo  [~] No icon file found - building without icon.
)

:: ============================================================
:: [4/5] Build the EXE
:: ============================================================
echo.
echo  [4/5] Building EXE (this takes 2-5 minutes)...
echo.

"%PYTHON_CMD%" -m PyInstaller ^
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

if not exist "%~dp0dist\KrakenPrime.exe" (
    echo  [!] dist\KrakenPrime.exe not found after build. Check errors above.
    pause & exit /b 1
)
echo.
echo  [OK] dist\KrakenPrime.exe built.

:: ============================================================
:: [5/5] Assemble the release package for end users
:: ============================================================
echo.
echo  [5/5] Assembling release package...

set RELEASE=%~dp0release\KrakenPrime
if exist "%~dp0release" rmdir /s /q "%~dp0release"
mkdir "%RELEASE%"

copy "%~dp0dist\KrakenPrime.exe" "%RELEASE%\KrakenPrime.exe"   >nul
copy "%~dp0setup.bat"            "%RELEASE%\setup.bat"         >nul
copy "%~dp0requirements.txt"     "%RELEASE%\requirements.txt"  >nul
copy "%~dp0adb.exe"              "%RELEASE%\adb.exe"           >nul
copy "%~dp0AdbWinApi.dll"        "%RELEASE%\AdbWinApi.dll"     >nul
copy "%~dp0AdbWinUsbApi.dll"     "%RELEASE%\AdbWinUsbApi.dll"  >nul
if exist "%~dp0README.txt" copy "%~dp0README.txt" "%RELEASE%\README.txt" >nul

:: Bundle the Tesseract installer so users can install offline
for %%F in ("%~dp0tesseract-ocr-w64-setup*.exe") do copy "%%F" "%RELEASE%\" >nul

:: Verify the release folder
set REL_OK=1
for %%F in (KrakenPrime.exe setup.bat requirements.txt adb.exe AdbWinApi.dll AdbWinUsbApi.dll) do (
    if not exist "%RELEASE%\%%F" (
        echo  [!] Release is missing: %%F
        set REL_OK=0
    )
)
if !REL_OK! EQU 1 (
    echo  [OK] Release package assembled.
) else (
    echo  [!] Release package is incomplete - see above.
)

:: ============================================================
:: Done
:: ============================================================
echo.
echo  ============================================================
echo    BUILD COMPLETE!
echo  ============================================================
echo.
pause