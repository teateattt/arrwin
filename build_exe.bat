@echo off
echo ========================================
echo   Windows Window Arranger - Build Script
echo ========================================
echo.

echo [1/5] Checking PyInstaller...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
    if errorlevel 1 (
        echo Failed to install PyInstaller!
        pause
        exit /b 1
    )
)

echo [2/5] Checking dependencies...
python -c "import pyvda" 2>nul
if errorlevel 1 (
    echo Installing pyvda...
    pip install pyvda
    if errorlevel 1 (
        echo Warning: Failed to install pyvda. Virtual desktop features will not work.
        echo Continuing build anyway...
    )
)

python -c "import sv_ttk, pywinstyles, darkdetect" 2>nul
if errorlevel 1 (
    echo Installing UI theme dependencies...
    pip install "sv-ttk==2.6.0" "pywinstyles==1.8" "darkdetect>=0.8,<1.0"
    if errorlevel 1 (
        echo Failed to install UI theme dependencies!
        pause
        exit /b 1
    )
)

echo [3/5] Cleaning old files...
if exist "build" rmdir /s /q "build"
if exist "dist\arrange_windows_gui.exe" del /f "dist\arrange_windows_gui.exe"
if exist "arrange_windows_gui.spec" del /f "arrange_windows_gui.spec"

echo [4/5] Building executable...
echo.

pyinstaller --onefile ^
    --windowed ^
    --name arrange_windows_gui ^
    --icon "assets\arrwin.ico" ^
    --clean ^
    --add-data "assets;assets" ^
    --hidden-import=pyvda ^
    --hidden-import=comtypes ^
    --hidden-import=pythoncom ^
    --hidden-import=win32gui ^
    --hidden-import=win32con ^
    --hidden-import=win32api ^
    --hidden-import=win32process ^
    --hidden-import=psutil ^
    --hidden-import=PIL ^
    --hidden-import=PIL.Image ^
    --hidden-import=PIL.ImageTk ^
    --hidden-import=pystray ^
    --collect-all pyvda ^
    --collect-all comtypes ^
    --collect-all pystray ^
    --collect-all sv_ttk ^
    arrange_windows_gui.py

if errorlevel 1 (
    echo.
    echo Build failed!
    pause
    exit /b 1
)

echo.
echo [5/5] Build successful!
echo.
copy /Y config.json dist\config.json >nul
if errorlevel 1 (
    echo Failed to copy config.json!
    exit /b 1
)
echo Output: dist\arrange_windows_gui.exe
echo.

if exist "dist\arrange_windows_gui.exe" (
    echo File size:
    dir "dist\arrange_windows_gui.exe" | find "arrange_windows_gui.exe"
    echo.
    echo You can run dist\arrange_windows_gui.exe
) else (
    echo Warning: exe file not found
)

echo.
echo ========================================
pause
