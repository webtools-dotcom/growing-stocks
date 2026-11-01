@echo off
rem Optional: schedule this after market close (~7 pm). The app has the same button.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python rising.py update >> data\update.log 2>&1
