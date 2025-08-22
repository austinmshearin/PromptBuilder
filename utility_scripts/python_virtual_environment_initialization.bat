@echo off
cd /d %~dp0
cd ..
for %%I in (.) do set CurrDirName=%%~nxI
set VirEnvName=%CurrDirName%_VirEnv
echo Python virtual environment will be set up in parent directory: %CurrDirName%
echo Python virtual environment will be named: %VirEnvName%
set /p dummy=Press enter to create virtual environment or close to cancel
echo Setting up Python virtual environment
python -m venv "%VirEnvName%"
echo Python virtual environment set up
set /p dummy=Press enter to close
