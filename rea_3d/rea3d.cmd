@echo off
setlocal
if defined REA3D_NODE (
  "%REA3D_NODE%" "%~dp0src\cli.mjs" %*
) else (
  node "%~dp0src\cli.mjs" %*
)
exit /b %errorlevel%
