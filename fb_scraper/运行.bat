@echo off
rem No chcp here: switching code page breaks non-ASCII folder paths in some cmd versions.
pushd "%~dp0"
if errorlevel 1 (
    echo Cannot open the script folder: %~dp0
    echo Please unzip FB-scraper to a normal folder first, e.g. D:\FBscraper
    pause
    exit /b 1
)
title FB page scraper

rem Find a working Python. The Microsoft Store "python" shortcut does not count.
set PY=
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >NUL 2>NUL && set PY=python
if not defined PY py -3 -c "import sys" >NUL 2>NUL && set PY=py -3
if not defined PY (
    echo Python 3.8+ was not found on this computer.
    echo Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" on the first install screen.
    pause
    exit /b 1
)
%PY% --version

echo [1/2] Checking Python packages...
%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo Default source failed. Retrying via Tsinghua mirror, bypassing the system proxy...
    rem Old pip fails on an https system proxy set by VPN tools, error: check_hostname requires server_hostname
    rem NO_PROXY=* makes pip connect directly
    set "NO_PROXY=*"
    %PY% -m pip install -q --disable-pip-version-check -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if errorlevel 1 (
    echo.
    echo Failed to install packages. Please send a screenshot of this window.
    pause
    exit /b 1
)
set "NO_PROXY="

echo [2/2] Running...
%PY% main.py
echo.
popd
pause
