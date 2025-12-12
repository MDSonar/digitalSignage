@echo off
REM Litmus Digital Signage - Quick Start Script for Windows

echo.
echo ========================================
echo   Litmus Digital Signage - Docker Setup
echo ========================================
echo.

REM Check if Docker is installed
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not installed. Please install Docker Desktop first:
    echo         https://docs.docker.com/desktop/windows/install/
    pause
    exit /b 1
)

REM Check if Docker Compose is installed
docker-compose --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Compose is not installed. Please install Docker Desktop first:
    echo         https://docs.docker.com/desktop/windows/install/
    pause
    exit /b 1
)

echo [OK] Docker and Docker Compose are installed
echo.

REM Create .env file if it doesn't exist
if not exist .env (
    echo Creating .env file from template...
    copy .env.example .env >nul
    echo [OK] Created .env file (please review and customize^)
    echo.
)

REM Create logo directory if it doesn't exist
if not exist logo (
    echo Creating logo directory...
    mkdir logo
    echo     Add your logo.png file to the 'logo' folder
    echo.
)

REM Menu
echo What would you like to do?
echo 1^) Build and start (production^)
echo 2^) Build and start (development with hot-reload^)
echo 3^) Stop services
echo 4^) View logs
echo 5^) Clean up (remove containers and volumes^)
echo.
set /p choice="Enter choice [1-5]: "

if "%choice%"=="1" goto production
if "%choice%"=="2" goto development
if "%choice%"=="3" goto stop
if "%choice%"=="4" goto logs
if "%choice%"=="5" goto cleanup
goto invalid

:production
echo.
echo Building and starting services (production^)...
docker-compose up -d --build
echo.
echo [OK] Services started!
echo      Dashboard: http://localhost:5000 (admin/signage^)
echo      Web Player: http://localhost:8080
echo.
echo To view logs: docker-compose logs -f
goto end

:development
echo.
echo Building and starting services (development^)...
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
echo.
echo [OK] Services started in development mode!
echo      Dashboard: http://localhost:5000 (admin/signage^)
echo      Web Player: http://localhost:8080
echo      Code changes will auto-reload
echo.
echo To view logs: docker-compose logs -f
goto end

:stop
echo.
echo Stopping services...
docker-compose down
echo [OK] Services stopped
goto end

:logs
echo.
echo Viewing logs (Ctrl+C to exit^)...
docker-compose logs -f
goto end

:cleanup
echo.
set /p confirm="WARNING: This will delete all containers and volumes. Continue? [y/N]: "
if /i "%confirm%"=="y" (
    echo Cleaning up...
    docker-compose down -v
    docker system prune -f
    echo [OK] Cleanup complete
) else (
    echo Cleanup cancelled
)
goto end

:invalid
echo [ERROR] Invalid choice
pause
exit /b 1

:end
echo.
echo ========================================
echo For more commands, see README-DOCKER.md
echo ========================================
pause
