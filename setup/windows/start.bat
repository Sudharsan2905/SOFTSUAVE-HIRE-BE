@echo off
echo ============================================
echo  Softsuave Hire BE - Starting (Windows)
echo ============================================

cd /d "%~dp0..\.."

if not exist ".venv" (
    echo [ERROR] Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo [INFO] Starting server on http://localhost:8000
echo [INFO] Swagger UI: http://localhost:8000/api/docs
echo [INFO] Press Ctrl+C to stop
echo.

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
