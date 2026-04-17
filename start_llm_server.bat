@echo off
REM ============================================================
REM LLM GPU Server - vLLM serve (RTX 5070, Qwen2.5-7B-Instruct-AWQ)
REM ============================================================
echo === LLM GPU Server Launcher ===
echo Model: Qwen2.5-7B-Instruct-AWQ (4-bit)
echo GPU:   RTX 5070 (12GB VRAM)
echo.

REM 이미 실행 중인지 확인
curl -s http://localhost:8000/health >nul 2>&1
if %errorlevel%==0 (
    echo Server already running!
    curl -s http://localhost:8000/health
    pause
    exit /b 0
)

echo Starting vLLM serve with GPU in WSL...
start /min wsl -d Ubuntu-24.04 -- bash -c "source ~/vllm-env/bin/activate && VLLM_FLASH_ATTN_VERSION=2 vllm serve /home/suzzi/models/Qwen2.5-7B-Instruct-AWQ --host 0.0.0.0 --port 8000 --api-key internal-token --gpu-memory-utilization 0.85 --max-model-len 4096 --quantization awq_marlin --enable-request-id-headers"

echo Waiting for model load (~30-60s)...
:wait_loop
timeout /t 5 /nobreak >nul
curl -s http://localhost:8000/health >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo Server is UP!
    curl -s http://localhost:8000/health
    echo.
    echo API: http://localhost:8000/v1/chat/completions
    pause
    exit /b 0
)
echo   still loading...
goto wait_loop
