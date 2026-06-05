@echo off
setlocal

powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync-code-to-vps.ps1" %*
exit /b %ERRORLEVEL%
