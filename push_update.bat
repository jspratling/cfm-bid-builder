@echo off
cd /d "%~dp0"
if exist .env (echo A .env file is in this folder. Move it up one folder before pushing. Nothing pushed. & pause & exit /b 1)
git add -A
git rm -r --cached --ignore-unmatch .env output data/past_bids.json >nul 2>nul
git status --short
git commit -m "Add real user trial results to report and slides" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01KYXq3UmjHQpiZ7YfcFMhLc"
git push origin main
echo.
echo Finished. Check https://github.com/jspratling/cfm-bid-builder
pause
