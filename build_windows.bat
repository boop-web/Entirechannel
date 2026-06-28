@echo off
REM Build ChannelArchiver.exe on Windows.
REM Requires Python 3.10+ installed (https://www.python.org/downloads/).
setlocal

echo Creating build environment...
python -m venv .buildenv || goto :err
call .buildenv\Scripts\activate.bat || goto :err

echo Installing dependencies...
python -m pip install --upgrade pip || goto :err
pip install -r requirements.txt -r requirements-build.txt || goto :err

echo Building the executable (this can take a few minutes)...
pyinstaller --noconfirm ChannelArchiver.spec || goto :err

echo.
echo ============================================================
echo  Done!  Your app is at:  dist\ChannelArchiver.exe
echo  Double-click it to launch. Videos save to your
echo  Videos\ChannelArchiver folder.
echo ============================================================
goto :eof

:err
echo.
echo Build failed. See the messages above.
exit /b 1
