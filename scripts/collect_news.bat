@echo off
setlocal
cd /d "C:\Users\AlexKwest\Documents\work\NewsTraderBot"
if not exist logs mkdir logs
.venv\Scripts\python.exe -m scripts.collect_news >> logs\collect_news.log 2>&1
