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
if not exist ".env" (
    echo [Setup] Creating .env file...
    (
        echo # Django Configuration
        echo DJANGO_SECRET_KEY=fundvault-django-secret-change-in-production
        echo DJANGO_DEBUG=true
        echo DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
        echo.
        echo # JWT Configuration
        echo JWT_SECRET=fundvault-secret-key-change-in-production
        echo.
        echo # Session Configuration
        echo SESSION_HOURS=24
        echo.
        echo # AI APIs - NVIDIA Nemotron (Primary)
        echo NVIDIA_API_KEY=
        echo NVIDIA_RECEIPT_MOCK=true
        echo.
        echo # AI APIs - Google Gemini (Fallback)
        echo GEMINI_API_KEY=
        echo GEMINI_RECEIPT_MOCK=true
    ) > .env
    echo [Setup] .env file created. Please update API keys as needed.
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
echo   - Backend: Django, DRF, JWT, bcrypt
echo   - AI: NVIDIA Nemotron (primary), Google Gemini (fallback)
echo   - Image Processing: Pillow
echo   - Configuration: python-dotenv
echo.
echo   Next Steps:
echo   1. Update .env file with your API keys (optional for mock mode)
echo   2. Run "run.bat" to start the development servers
echo   3. Open http://localhost:3001 in your browser
echo.
echo ============================================================
echo.
pause
