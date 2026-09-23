@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
call run-python.cmd printer.py listen --print
pause
