@echo off
cd /d "%~dp0"
where git >nul 2>nul || (echo Git is not installed. Install it from https://git-scm.com/download/win and run this again. & pause & exit /b 1)
if exist .env (echo A .env file is in this folder. Move it up one folder before pushing. Nothing pushed. & pause & exit /b 1)
if not exist .git git init -b main
git config user.name "jspratling"
git config user.email "jspratling@users.noreply.github.com"
git add -A
git rm -r --cached --ignore-unmatch .env output data/past_bids.json >nul 2>nul
echo.
echo Files going to GitHub:
git status --short
git commit -m "CFM Bid Builder: MGT 409 final project"
git branch -M main
git remote remove origin >nul 2>nul
git remote add origin https://github.com/jspratling/cfm-bid-builder.git
echo.
echo If a GitHub sign-in window opens, choose "Sign in with your browser" and click Authorize.
git push -u origin main
echo.
echo Finished. Check https://github.com/jspratling/cfm-bid-builder
pause
