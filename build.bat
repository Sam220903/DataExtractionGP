@echo off
setlocal enableextensions
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem Se compila en una carpeta LOCAL (fuera de OneDrive) para que OneDrive, el
rem antivirus o el indexador de Windows no bloqueen los archivos recien creados
rem (causa del PermissionError WinError 5 / WinError 32). Al final se copia el
rem resultado a dist\DataExtractionGP sin borrar tu .env, data ni outputs.
rem ---------------------------------------------------------------------------
set "STAGE=%LOCALAPPDATA%\DataExtractionGP_build"
set "STAGE_DIST=%STAGE%\dist"
set "STAGE_WORK=%STAGE%\work"
set "TARGET=%~dp0dist\DataExtractionGP"

echo === Cerrando instancias en ejecucion de DataExtractionGP.exe ===
taskkill /F /T /IM DataExtractionGP.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo === Limpiando compilaciones anteriores ===
call :rmdir_retry "%STAGE%"
if errorlevel 1 (
    echo No se pudo borrar "%STAGE%".
    echo Cierra cualquier ventana o programa que use esa carpeta y vuelve a intentar.
    exit /b 1
)
rem Carpeta build antigua dentro del proyecto: ya no se usa, se intenta borrar sin ser critico
if exist "%~dp0build" call :rmdir_retry "%~dp0build"

echo === Compilando con PyInstaller ===
pyinstaller --noconfirm --clean --distpath "%STAGE_DIST%" --workpath "%STAGE_WORK%" DataExtractionGP.spec
if errorlevel 1 (
    echo Error al compilar con PyInstaller.
    exit /b 1
)

set "BUILT=%STAGE_DIST%\DataExtractionGP"

echo === Copiando el codigo junto al .exe ===
for %%F in (scrappers writers verification lib extractors prompts) do (
    robocopy "%%F" "%BUILT%\%%F" /E /XD __pycache__ >nul
    if errorlevel 8 (
        echo Error al copiar la carpeta %%F.
        exit /b 1
    )
)

echo === Publicando en dist\DataExtractionGP ===
if not exist "%~dp0dist" mkdir "%~dp0dist"
if not exist "%TARGET%" mkdir "%TARGET%"
rem /MIR deja dist igual que la compilacion, pero NO toca .env, data ni outputs del destino
robocopy "%BUILT%" "%TARGET%" /MIR /XD "%TARGET%\data" "%TARGET%\outputs" /XF .env /R:5 /W:3 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Error al copiar el resultado a dist.
    echo Si dist esta dentro de OneDrive, pausa la sincronizacion unos minutos y reintenta.
    exit /b 1
)

echo === Creando carpetas de resultados ===
if not exist "%TARGET%\data" mkdir "%TARGET%\data"
if not exist "%TARGET%\outputs" mkdir "%TARGET%\outputs"

echo.
echo Listo: %TARGET%\DataExtractionGP.exe
echo Recuerda colocar tu archivo .env junto al .exe antes de usarlo.
endlocal
exit /b 0

rem ---------------------------------------------------------------------------
rem Borra una carpeta con reintentos: en Windows un archivo puede seguir bloqueado
rem unos segundos despues de cerrar el programa que lo usaba.
rem ---------------------------------------------------------------------------
:rmdir_retry
set "RM_PATH=%~1"
set /a RM_TRY=0
:rmdir_loop
if not exist "%RM_PATH%" exit /b 0
rmdir /s /q "%RM_PATH%" >nul 2>&1
if not exist "%RM_PATH%" exit /b 0
set /a RM_TRY+=1
if %RM_TRY% GEQ 6 exit /b 1
timeout /t 3 /nobreak >nul
goto rmdir_loop