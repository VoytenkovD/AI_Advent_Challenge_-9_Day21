@echo off
rem llmcli - CLI for the local LLM in Ollama. Example: llmcli ask --stats "Privet"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python "%~dp0llmcli.py" %*
