@echo off
start "" wt.exe -w -1 --maximized new-tab --title "Antigravity Workbench" wsl.exe -d Ubuntu --cd "~" --exec bash -lc "~/.local/bin/agy-herdr-projects --choose"
