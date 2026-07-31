@echo off
setlocal
cd /d "C:\Users\AlexKwest\Documents\work\NewsTraderBot"
if not exist logs mkdir logs
.venv\Scripts\pythonw.exe -m scripts.run_app >> logs\app.log 2>&1
