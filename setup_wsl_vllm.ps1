# ============================================================
# vLLM on WSL2 설치 스크립트
# 관리자 PowerShell에서 실행: .\setup_wsl_vllm.ps1
# ============================================================

Write-Host "=== Step 1: WSL2 설치 ===" -ForegroundColor Cyan
wsl --install -d Ubuntu

Write-Host ""
Write-Host "=== WSL 설치 완료 후 재부팅이 필요합니다 ===" -ForegroundColor Yellow
Write-Host "재부팅 후 Ubuntu 터미널을 열고 아래 명령을 순서대로 실행하세요:" -ForegroundColor Yellow
Write-Host ""
Write-Host @"

# === Step 2: Ubuntu 내부에서 실행 ===

# Python 환경 설정
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip
python3.11 -m venv ~/vllm-env
source ~/vllm-env/bin/activate

# vLLM 설치 (CPU 모드)
pip install vllm --extra-index-url https://download.pytorch.org/whl/cpu

# 모델 다운로드 (Qwen2.5-3B, CPU용 경량 모델)
pip install huggingface-hub
huggingface-cli download Qwen/Qwen2.5-3B-Instruct --local-dir ~/models/qwen2.5-3b

# vLLM 서버 시작 (CPU 모드)
vllm serve ~/models/qwen2.5-3b \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key internal-token \
  --generation-config vllm \
  --device cpu \
  --dtype float32 \
  --max-model-len 4096 \
  --max-num-seqs 4

# === Step 3: Windows에서 테스트 ===
# python -m runtime_llm_gateway.tests.test_gateway_e2e

"@ -ForegroundColor Green
