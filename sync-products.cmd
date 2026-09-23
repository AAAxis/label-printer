@echo off
cd /d "%~dp0"
call run-python.cmd printer.py sync-products
pause
