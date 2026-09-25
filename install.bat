@echo off
REM FundVault - Complete Installation Script
REM This script installs all dependencies for the FundVault Full Stack Application

title FundVault - Installing All Dependencies
cls

echo.
echo ============================================================
echo.
echo     FundVault - Full Stack Installation
echo     Fund Management System with AI Receipt Extraction
echo.
echo ============================================================
echo.

cd /d "%~dp0"

REM Check if Node.js is installed
where node >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Node.js is not installed or not in PATH.
    echo Please install Node.js from https://nodejs.org/
    echo.
    pause
    exit /b 1
)

REM Check if Python is installed
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org/
    echo.
    pause
    exit /b 1
)

echo [Prerequisites Check] ✓ Node.js and Python found
echo.

REM Check if .env file exists
if not exist "backend\.env" (
    echo [Setup] Creating backend\.env from backend\.env.example...
    copy /Y backend\.env.example backend\.env >nul
    echo [Setup] backend\.env file created. Please update API keys as needed.
    echo.
)

echo [1/3] Installing root dependencies (npm)...
call npm install --prefer-offline
if %ERRORLEVEL% neq 0 (
    echo ERROR: Root npm install failed.
    echo Please check your npm installation and internet connection.
    echo.
    pause
    exit /b 1
)
echo [1/3] ✓ Root dependencies installed
echo.

echo [2/3] Installing frontend dependencies (Next.js, React, Chart.js, jsPDF)...
call npm --prefix frontend install --prefer-offline
if %ERRORLEVEL% neq 0 (
    echo ERROR: Frontend npm install failed.
    echo Please check your npm installation and internet connection.
    echo.
    pause
    exit /b 1
)
echo [2/3] ✓ Frontend dependencies installed
echo.

echo [3/3] Installing backend dependencies (Django, AI APIs, Pillow)...
echo This may take a few moments...
pip install -r backend\requirements.txt --quiet
if %ERRORLEVEL% neq 0 (
    echo ERROR: Backend pip install failed.
    echo Please check your Python installation and internet connection.
    echo.
    pause
    exit /b 1
)
echo [3/3] ✓ Backend dependencies installed
echo.

echo ============================================================
echo.
echo   ✓ Installation Complete!
echo.
echo   What's installed:
echo   - Frontend: Next.js, React, Chart.js, jsPDF
echo   - Backend: Django, JWT, bcrypt, per-org AI + storage clients
echo   - Image Processing: Pillow
echo   - Configuration: python-dotenv
echo.
echo   Next Steps:
echo   1. Run "run.bat" — it brings up the two local Postgres databases
echo      (docker compose up -d) alongside the dev servers.
echo   2. backend\.env needs a FUNDVAULT_SECRET_KEY before you create your
echo      first organisation. Generate one with:
echo        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
echo      and paste it in as FUNDVAULT_SECRET_KEY=... in backend\.env.
echo   3. There are no AI or storage keys to set here — each organisation
echo      configures its own AI provider and receipt storage from its
echo      settings page in the app, after you create or join one.
echo   4. Open http://localhost:3001 in your browser.
echo.
echo ============================================================
echo.
pause
