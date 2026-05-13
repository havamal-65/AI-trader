@echo off
start "AI-Trader Monitor" cmd /k python "%~dp0monitor.py" %*
