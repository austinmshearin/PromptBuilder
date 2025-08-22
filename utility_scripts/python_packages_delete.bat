@echo off
cd /d %~dp0
cd ..
for %%I in (.) do set CurrDirName=%%~nxI
set VirEnvName=%CurrDirName%_VirEnv
call "./%VirEnvName%/Scripts/activate.bat"
echo All Python packages will be deleted from the Python virtual environment
set /p dummy=Press enter to confirm delete or close to cancel
echo Deleting all Python packages
python -m pip freeze > delete.txt
python -m pip uninstall -r delete.txt -y
del delete.txt
echo Deleted all Python packages from Python virtual environment
set /p dummy=Press enter to close
