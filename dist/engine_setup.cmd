@echo off
color 09
title LocalReader Plus - Engine Manager

:MENU
cls
echo =======================================================
echo    LocalReader Plus - ONNX Engine Setup
echo =======================================================
echo.
echo [1] CPU VERSION (Standard) 
echo     - Uninstalls GPU versions, installs standard CPU ONNX. 
echo.
echo [2] GPU VERSION 
echo     - Uninstalls CPU version, installs ONNX GPU (no CUDA/cuDNN included).
echo.
echo [3] ONNX-GPU [CUDA, cuDNN] 
echo     - Uninstalls CPU version, install CUDA and cuDNN DLLS from NVIDIA site alongside the onnxruntime-gpu package.
echo.
echo [0] Exit
echo =======================================================
set /p choice="Type 1, 2, 3, or 0 to Exit and press Enter: "

if "%choice%"=="0" exit /b 0

:: Validate input before running uninstall
if not "%choice%"=="1" if not "%choice%"=="2" if not "%choice%"=="3" (
    echo.
    echo [ERROR] Invalid selection. Please try again.
    timeout /t 2 >nul
    goto MENU
)

echo.
echo Cleaning existing configurations...
call uv pip uninstall -y onnxruntime onnxruntime-gpu >nul 2>&1

if "%choice%"=="1" goto SETUP_CPU
if "%choice%"=="2" goto SETUP_GPU
if "%choice%"=="3" goto SETUP_CUDA

:SETUP_CPU
echo Installing CPU Engine...
call uv pip install onnxruntime
goto FINISH

:SETUP_GPU
echo Installing GPU (Manual)...
call uv pip install onnxruntime-gpu
goto FINISH

:SETUP_CUDA
echo Installing GPU (Auto CUDA)...
call uv pip install "onnxruntime-gpu[cuda,cudnn]"
goto FINISH

:FINISH
echo.
echo [SUCCESS] Engine Swap Complete.
echo Closing in 5 seconds...
timeout /t 5 >nul