@echo off
title FundVault - Running Application
echo ============================================
echo   FundVault - Starting Application
echo ============================================
echo.

cd /d "%~dp0"

echo Starting development databases...
docker compose up -d >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo WARNING: could not start Docker databases.
    echo Set DATABASE_URL and DEV_TENANT_DATABASE_URL in backend\.env to use your own Postgres.
    echo.
)

echo Applying control-plane database migrations...
python backend\manage.py migrate --database=default
if %ERRORLEVEL% neq 0 (
    echo WARNING: control-plane migration failed. Check that the database is
    echo reachable ^(see backend\.env^) and re-run run.bat.
    echo.
)

echo Opening FundVault in your browser...
timeout /t 3 /nobreak >nul
start http://localhost:3001

echo Starting backend (Django :8000) + frontend (Next.js :3001)...
echo Press Ctrl+C to stop both servers.
echo.

call npm run dev
