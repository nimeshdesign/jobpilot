@echo off
REM Double-clickable setup for people who would rather not touch PowerShell.
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
