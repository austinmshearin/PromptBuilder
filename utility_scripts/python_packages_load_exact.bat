@echo off
cd /d %~dp0
cd ..
for %%I in (.) do set CurrDirName=%%~nxI
set VirEnvName=%CurrDirName%_VirEnv
call "./%VirEnvName%/Scripts/activate.bat"
echo Loading all Python packages from requirements_exact.txt into Python virtual environment
set /p dummy=Press enter to continue or close to cancel
echo Loading Python packages
python -m pip install -r requirements_exact.txt
echo Loaded Python packages
set /p dummy=Press enter to close
