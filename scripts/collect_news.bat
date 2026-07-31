@echo off
setlocal
cd /d "C:\Users\AlexKwest\Documents\work\NewsTraderBot"
if not exist logs mkdir logs
.venv\Scripts\python.exe -m scripts.daily_pipeline >> logs\daily_pipeline.log 2>&1
