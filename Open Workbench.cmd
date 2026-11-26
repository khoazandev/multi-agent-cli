@echo off
if exist "%~dp0Multi Agent CLI.exe" (
  start "" "%~dp0Multi Agent CLI.exe"
  exit /b
)
if exist "%~dp0desktop\target\release\multi-agent-cli-desktop.exe" (
  start "" "%~dp0desktop\target\release\multi-agent-cli-desktop.exe"
  exit /b
)
start "" wt.exe -w multi-agent-cli --maximized new-tab --title "Multi Agent CLI" wsl.exe -d Ubuntu --cd "~" --exec bash -lc "~/.local/bin/agy-herdr-projects --manage"
