@echo off
title FundVault - Running Application
echo ============================================
echo   FundVault - Starting Application
echo ============================================
echo.

cd /d "%~dp0"

echo Opening FundVault in your browser...
timeout /t 3 /nobreak >nul
start http://localhost:3001

echo Starting backend (Django :8000) + frontend (Next.js :3001)...
echo Press Ctrl+C to stop both servers.
echo.

call npm run dev
