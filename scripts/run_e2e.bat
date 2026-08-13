@echo off
setlocal
cd /d "%~dp0.."
if not exist "node_modules\@playwright\test\cli.js" (
    echo [e2e] node_modules\@playwright\test не найден — устанавливаю зависимости (npm install)...
    call npm install
    if errorlevel 1 (
        echo [e2e] Ошибка npm install. Установите Node.js 22+ и выполните "npm install" вручную.
        exit /b 1
    )
)
call npx playwright test %*
