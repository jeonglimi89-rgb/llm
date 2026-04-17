#!/bin/bash
# ============================================================
# vLLM CPU 모드 설치 스크립트
# Ubuntu WSL 터미널에서 실행:
#   cd /mnt/c/Users/SUZZI/Downloads/LLM
#   bash setup_vllm.sh
# ============================================================

set -e

echo "=== Step 1: System packages ==="
sudo apt update -y
sudo apt install -y python3-pip python3-venv curl

echo ""
echo "=== Step 2: Python venv ==="
python3 -m venv ~/vllm-env
source ~/vllm-env/bin/activate

echo ""
echo "=== Step 3: Install vLLM (CPU) ==="
pip install --upgrade pip
pip install vllm --extra-index-url https://download.pytorch.org/whl/cpu 2>&1 || {
    echo "[WARN] vLLM CPU wheel 없음, PyTorch CPU + transformers로 대체 설치"
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install transformers accelerate huggingface-hub openai fastapi uvicorn
    echo "[INFO] 대체 서버로 진행합니다"
}

echo ""
echo "=== Step 4: Download model (Qwen2.5-3B-Instruct) ==="
pip install huggingface-hub
huggingface-cli download Qwen/Qwen2.5-3B-Instruct --local-dir ~/models/qwen2.5-3b-instruct

echo ""
echo "=== Step 5: Start vLLM server ==="
echo "서버 시작 명령 (별도 터미널에서 실행):"
echo ""
echo "  source ~/vllm-env/bin/activate"
echo "  vllm serve ~/models/qwen2.5-3b-instruct \\"
echo "    --host 0.0.0.0 --port 8000 \\"
echo "    --api-key internal-token \\"
echo "    --device cpu --dtype float32 \\"
echo "    --max-model-len 2048 \\"
echo "    --max-num-seqs 2"
echo ""
echo "또는 vLLM 설치 실패 시 대체 서버:"
echo ""
echo "  source ~/vllm-env/bin/activate"
echo "  python3 /mnt/c/Users/SUZZI/Downloads/LLM/fallback_server.py"
echo ""
echo "=== 설치 완료 ==="
