@echo off
cd /d "%~dp0"
call run-python.cmd install_saved_labels.py
pause
