@echo off
cd /d "%~dp0"
if not exist report\ab_test mkdir report\ab_test
set PYDANTIC_AI_NO_BANNER=1
echo Running the photo size test (about 2 minutes)...
.venv\Scripts\python report\ab_photo_test.py > report\ab_test\ab_log.txt 2>&1
type report\ab_test\ab_log.txt
echo.
echo Finished. You can close this window.
pause
