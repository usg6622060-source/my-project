@echo off
chcp 65001 >nul
cd /d "%~dp0"
title FB page scraper

set PY=python
where python >/dev/null 2>/dev/null || set PY=py

echo [1/2] Checking Python packages...
%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Default source failed, trying Tsinghua mirror...
    %PY% -m pip install -q --disable-pip-version-check -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if errorlevel 1 (
    echo.
    echo Failed to install packages. Please send a screenshot of this window.
    pause
    exit /b 1
)

echo [2/2] Running...
%PY% main.py
echo.
pause
