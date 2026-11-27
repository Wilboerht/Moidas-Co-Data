@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PORT=8501"
set "RULE_NAME=Co-data-LAN-%PORT%"

echo ==========================================
echo   Co-data React Panel (LAN mode)
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

rem Inject the current LAN IPs into the allowlists for this run, so the panel
rem keeps working after DHCP changes the address.
set "LAN_HOSTS="
set "LAN_ORIGINS="
set "LAN_URL_LIST="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\lan_ips.ps1"`) do (
    if defined LAN_HOSTS (
        set "LAN_HOSTS=!LAN_HOSTS!,%%i"
        set "LAN_ORIGINS=!LAN_ORIGINS!,http://%%i:%PORT%"
    ) else (
        set "LAN_HOSTS=%%i"
        set "LAN_ORIGINS=http://%%i:%PORT%"
    )
    set "LAN_URL_LIST=!LAN_URL_LIST! http://%%i:%PORT%"
)
if not defined LAN_HOSTS (
    echo [ERROR] No LAN IPv4 address found. Connect to Wi-Fi or Ethernet first.
    pause
    exit /b 1
)

set "CODATA_ALLOWED_HOSTS=127.0.0.1,localhost,::1,!LAN_HOSTS!"
set "CODATA_ALLOWED_ORIGINS=http://127.0.0.1:%PORT%,http://localhost:%PORT%,http://[::1]:%PORT%,!LAN_ORIGINS!"
set "CODATA_COOKIE_SECURE=false"

netsh advfirewall firewall show rule name="%RULE_NAME%" >nul 2>nul
if errorlevel 1 (
    echo [0/4] Adding Windows Firewall rule ^(LAN only^), please accept the UAC prompt...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -WindowStyle Hidden -FilePath netsh.exe -ArgumentList 'advfirewall','firewall','add','rule','name=%RULE_NAME%','dir=in','action=allow','protocol=TCP','localport=%PORT%','remoteip=localsubnet'"
    echo       If the UAC prompt was declined, other devices cannot connect; re-run and accept it.
    echo.
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

echo [4/4] Starting server on all interfaces, port %PORT%...
echo.
echo   Local: http://127.0.0.1:%PORT%
for %%i in (!LAN_URL_LIST!) do echo   LAN:   %%i
echo.
echo Share a LAN URL above. Every user needs an account created in User Management.
echo Keep this window open; close it to stop the panel.
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:%PORT%'"
"%UV%" run codata-web --host 0.0.0.0 --port %PORT%
if errorlevel 1 (
    echo.
    echo [ERROR] Server failed to start. See logs above.
    pause
)

endlocal
