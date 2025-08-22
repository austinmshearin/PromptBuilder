@echo off
cd /d %~dp0
cd ..
for %%I in (.) do set CurrDirName=%%~nxI
set VirEnvName=%CurrDirName%_VirEnv
call "./%VirEnvName%/Scripts/activate.bat"
echo Exporting all Python packages from virtual environment to requirements_exact.txt
echo This is an overwriting operation
set /p dummy=Press enter to export Python packages or close to cancel
echo Exporting Python packages
python -m pip freeze > requirements_exact.txt
echo Exported Python packages
set /p dummy=Press enter to close
