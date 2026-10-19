@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title 地形桥接 一键安装

echo.
echo ==========================================
echo   Gaea - Houdini - UE5 地形桥接
echo   一键安装
echo ==========================================
echo.

set "PY="
where python >nul 2>nul
if not errorlevel 1 set "PY=python"

if not defined PY (
    for %%V in (5.6 5.5 5.4 5.3) do (
        if not defined PY (
            set "CAND=C:\Program Files\Epic Games\UE_%%V\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
            if exist "!CAND!" set "PY=!CAND!"
        )
    )
)

if not defined PY (
    echo [错误] 找不到 Python。
    echo   请安装 Python 3,或先安装 Unreal Engine 5.6 ^(它自带 Python^)。
    echo.
    pause
    exit /b 1
)

echo 使用 Python: %PY%
echo.

"%PY%" "%~dp0install.py"

echo.
pause
