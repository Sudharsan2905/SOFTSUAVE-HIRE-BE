@echo off
echo ============================================
echo  Softsuave Hire BE - Windows Setup
echo ============================================

:: Move to project root (two levels up from setup\windows\)
cd /d "%~dp0..\.."

:: Check if uv is installed
uv --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] uv not found. Installing uv...
    powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
) else (
    echo [OK] uv found
    uv --version
)

:: Create virtual environment pinned to Python 3.12
if not exist ".venv" (
    echo [INFO] Creating virtual environment with Python 3.12...
    uv venv --python 3.12
) else (
    echo [OK] Virtual environment already exists
)

:: Install all dependencies (base + dev)
echo [INFO] Installing dependencies...
uv pip install -r requirements/dev.txt
echo [OK] Dependencies installed

:: Copy .env if not present
if not exist ".env" (
    copy .env.example .env
    echo [OK] .env created from .env.example
    echo [ACTION] Open .env and fill in your secrets before starting
) else (
    echo [OK] .env already exists
)

:: Activate venv and install pre-commit hooks
call .venv\Scripts\activate.bat
echo [INFO] Installing pre-commit hooks...
pre-commit install
echo [OK] Pre-commit hooks installed

echo.
echo ============================================
echo  Setup complete!
echo  Next: run setup\windows\start.bat
echo ============================================
pause
