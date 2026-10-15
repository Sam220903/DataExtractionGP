@echo off
setlocal
cd /d "%~dp0"

echo === Limpiando compilaciones anteriores ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo === Compilando con PyInstaller ===
pyinstaller --noconfirm --clean DataExtractionGP.spec
if errorlevel 1 (
    echo Error al compilar con PyInstaller.
    exit /b 1
)

set TARGET=dist\DataExtractionGP

echo === Copiando el codigo junto al .exe ===
for %%F in (scrappers writers verification lib extractors prompts) do (
    robocopy "%%F" "%TARGET%\%%F" /E /XD __pycache__ >nul
)

echo === Creando carpetas de resultados ===
if not exist "%TARGET%\data" mkdir "%TARGET%\data"
if not exist "%TARGET%\outputs" mkdir "%TARGET%\outputs"

echo.
echo Listo: %TARGET%\DataExtractionGP.exe
echo Recuerda colocar tu archivo .env junto al .exe antes de usarlo.
endlocal