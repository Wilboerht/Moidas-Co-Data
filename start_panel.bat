@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   Co-data React Panel
echo ==========================================
echo.

set "UV=uv"
where uv >nul 2>nul
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV=%USERPROFILE%\.local\bin\uv.exe"
    ) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
        set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
    ) else (
        echo [ERROR] uv not found. Install from https://docs.astral.sh/uv/
        pause
        exit /b 1
    )
)

if not exist ".env" (
    echo [ERROR] .env not found.
    echo Copy .env.example to .env and fill DATAFLOW_HOST / DATAFLOW_TOKEN or RNOTE_API_KEY.
    pause
    exit /b 1
)

echo [1/4] Syncing Python dependencies...
"%UV%" sync --frozen --quiet
if errorlevel 1 (
    echo [ERROR] uv sync failed. Check network or uv config.
    pause
    exit /b 1
)

echo [2/4] Building React frontend...
where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js/npm not found. Install Node.js 22+ and retry.
    pause
    exit /b 1
)
pushd frontend
if not exist "node_modules" (
    call npm ci
    if errorlevel 1 (
        popd
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
)
call npm run build
if errorlevel 1 (
    popd
    echo [ERROR] npm run build failed.
    pause
    exit /b 1
)
popd

echo [3/4] Checking admin account...
"%UV%" run codata-admin ensure-admin
if errorlevel 1 (
    echo [ERROR] Admin initialization failed.
    pause
    exit /b 1
)

echo [4/4] Starting server at http://127.0.0.1:8501
echo Browser will open automatically. Close this window to stop the panel.
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8501'"
"%UV%" run codata-web
if errorlevel 1 (
    echo.
    echo [ERROR] Server failed to start. See logs above.
    pause
)

endlocal
