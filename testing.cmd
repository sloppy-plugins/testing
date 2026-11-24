@echo off
setlocal
python "%~dp0testing.py" %*
exit /b %errorlevel%
