# ============================================================
# LLM GPU Server - vLLM serve (RTX 5070, Qwen2.5-7B-Instruct-AWQ)
# PowerShell에서 실행: .\start_llm_server.ps1
# ============================================================

$MODEL = "/home/suzzi/models/Qwen2.5-7B-Instruct-AWQ"
$PORT = 8000
$HEALTH_URL = "http://localhost:$PORT/health"

Write-Host "=== LLM GPU Server Launcher ===" -ForegroundColor Cyan
Write-Host "Model: Qwen2.5-7B-Instruct-AWQ (4-bit)" -ForegroundColor White
Write-Host "GPU:   RTX 5070 (12GB VRAM)" -ForegroundColor White
Write-Host "Port:  $PORT" -ForegroundColor White
Write-Host ""

# 1. 이미 실행 중인지 확인
try {
    $health = Invoke-RestMethod -Uri $HEALTH_URL -TimeoutSec 3 -ErrorAction Stop
    Write-Host "Server already running!" -ForegroundColor Green
    Write-Host "  Status: $($health.status)"
    Write-Host "  Model:  $($health.model)"
    exit 0
} catch {
    Write-Host "Server not running. Starting vLLM GPU serve..." -ForegroundColor Yellow
}

# 2. WSL에서 vLLM serve 시작 (GPU)
Write-Host ""
Write-Host "Starting vLLM serve with GPU..." -ForegroundColor Cyan
$vllmCmd = "source ~/vllm-env/bin/activate && VLLM_FLASH_ATTN_VERSION=2 vllm serve $MODEL --host 0.0.0.0 --port $PORT --api-key internal-token --gpu-memory-utilization 0.85 --max-model-len 4096 --quantization awq_marlin --enable-request-id-headers"
Start-Process wsl -ArgumentList "-d", "Ubuntu-24.04", "--", "bash", "-c", $vllmCmd -WindowStyle Minimized

# 3. 서버 준비 대기 (vLLM 로딩은 ~30-60초)
Write-Host "Waiting for vLLM to load model on GPU..." -ForegroundColor Yellow
for ($i = 1; $i -le 36; $i++) {
    Start-Sleep -Seconds 5
    try {
        $health = Invoke-RestMethod -Uri $HEALTH_URL -TimeoutSec 3 -ErrorAction Stop
        Write-Host ""
        Write-Host "vLLM GPU Server is UP! ($($i*5)s)" -ForegroundColor Green
        Write-Host "  Health: $($health.status)"
        Write-Host ""
        Write-Host "API Endpoint: http://localhost:$PORT/v1/chat/completions" -ForegroundColor Cyan
        Write-Host "Health Check: $HEALTH_URL" -ForegroundColor Cyan
        Write-Host "OpenAI Compat: http://localhost:$PORT/v1/models" -ForegroundColor Cyan
        exit 0
    } catch {
        Write-Host "  $($i*5)s... loading" -NoNewline -ForegroundColor DarkGray
        Write-Host ""
    }
}

Write-Host "Server failed to start within 180s!" -ForegroundColor Red
Write-Host "Check WSL logs: wsl -d Ubuntu-24.04 -- bash -c 'source ~/vllm-env/bin/activate && vllm serve --help'" -ForegroundColor Yellow
exit 1
