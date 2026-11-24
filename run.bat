@echo off
title Meowtrix AI by Mohsin Khawaja
cd /d "%~dp0"

echo ======================================================================
echo                   🐱 Meowtrix AI by Mohsin Khawaja
echo           "100% Offline Multi-GGUF Local AI Studio (CPU)"
echo                   Follow Instagram: @csib.mohsin
echo ======================================================================
echo.

:: Check Python installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your system PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    pause
    exit /b 1
)

:: Ensure required directories exist
if not exist "models" mkdir "models"
if not exist "data" mkdir "data"

:: Check and auto-install dependencies
python -c "import llama_cpp, fastapi, uvicorn, pydantic" >nul 2>&1
if %errorlevel% neq 0 (
    echo [SETUP] Installing required dependencies (CPU-optimized llama-cpp-python)...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
    echo [SETUP] Dependencies installed successfully!
    echo.
)

echo Starting Meowtrix AI Local Server...
echo Web Dashboard: http://127.0.0.1:8000
echo.
python main.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Meowtrix AI terminated unexpectedly (exit code %errorlevel%).
    pause
)
