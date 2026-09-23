@echo off
set PYTHONUTF8=1
where py >nul 2>&1
if not errorlevel 1 (
    py -3 %*
    exit /b
)
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" %*
    exit /b
)
echo Python was not found. Install Python 3.13, then close and reopen this window.
exit /b 1
