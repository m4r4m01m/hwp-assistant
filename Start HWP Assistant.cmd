@echo off
setlocal
rem Prefer Windows PowerShell's own standard modules when launched from pwsh.
set "PSModulePath=%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules;%ProgramFiles%\WindowsPowerShell\Modules;%UserProfile%\Documents\WindowsPowerShell\Modules"
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1" %*
set "hwp_exit=%errorlevel%"
if not "%hwp_exit%"=="0" (
  echo.
  echo Setup failed. See the message above and docs\developer-quickstart.md.
  if not defined CI pause
)
endlocal & exit /b %hwp_exit%
