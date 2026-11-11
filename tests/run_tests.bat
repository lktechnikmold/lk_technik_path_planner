@echo off
rem ------------------------------------------------------------------
rem  Alle Tests des Plugins unter QGIS 3 und QGIS 4 ausfuehren.
rem
rem  Gesucht wird jeweils die neueste Installation unter
rem  "C:\Program Files\QGIS 3.*" bzw. "C:\Program Files\QGIS 4.*".
rem  Andere Pfade ueber Umgebungsvariablen QGIS3_PY / QGIS4_PY
rem  (Pfad zu python-qgis-ltr.bat bzw. python-qgis.bat).
rem
rem  Weitere Argumente werden an run_tests.py weitergegeben,
rem  z. B.  tests\run_tests.bat test_tracks -v
rem  Rueckgabe 0 = alle Tests in allen gefundenen Versionen bestanden.
rem ------------------------------------------------------------------
setlocal EnableDelayedExpansion
cd /d "%~dp0\.."

set "PY3=%QGIS3_PY%"
if not defined PY3 (
    for /d %%D in ("%ProgramFiles%\QGIS 3.*") do (
        if exist "%%D\bin\python-qgis-ltr.bat" (
            set "PY3=%%D\bin\python-qgis-ltr.bat"
        ) else if exist "%%D\bin\python-qgis.bat" (
            set "PY3=%%D\bin\python-qgis.bat"
        )
    )
)
set "PY4=%QGIS4_PY%"
if not defined PY4 (
    for /d %%D in ("%ProgramFiles%\QGIS 4.*") do (
        if exist "%%D\bin\python-qgis.bat" (
            set "PY4=%%D\bin\python-qgis.bat"
        ) else if exist "%%D\bin\python-qgis-ltr.bat" (
            set "PY4=%%D\bin\python-qgis-ltr.bat"
        )
    )
)

set "FAILED=0"
set "RAN=0"
call :run "QGIS 3" "%PY3%" %*
call :run "QGIS 4" "%PY4%" %*

echo.
if "%RAN%"=="0" (
    echo FEHLER: keine QGIS-Installation gefunden.
    exit /b 1
)
if "%FAILED%"=="1" (
    echo ERGEBNIS: Tests FEHLGESCHLAGEN.
    exit /b 1
)
echo ERGEBNIS: alle Tests bestanden.
exit /b 0

:run
set "LABEL=%~1"
set "PY=%~2"
shift
shift
if "%PY%"=="" (
    echo [%LABEL%] nicht installiert - uebersprungen.
    exit /b 0
)
echo.
echo ===== %LABEL%: %PY%
call "%PY%" tests\run_tests.py %1 %2 %3 %4 %5 %6 %7 %8 %9
if errorlevel 1 set "FAILED=1"
set /a RAN+=1
exit /b 0
