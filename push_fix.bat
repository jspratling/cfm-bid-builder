@echo off
rem Replaces the first commit (removes a photographer name from one run file) and force-pushes.
cd /d "%~dp0"
git add -A
git commit --amend --no-edit
git push --force origin main
echo.
echo Finished. Check https://github.com/jspratling/cfm-bid-builder
pause
