# LLM Orchestration Platform

## Architecture

4개 도메인(Minecraft, Builder/CAD, Product Design, Drawing/Animation)에 자연어 → 구조화 JSON 파이프라인을 제공하는 LLM 오케스트레이션 플랫폼.

```
User Input → Domain Classifier → Task Router → LLM (vLLM GPU) → Schema Validation → Response
                                                     ↓
                                              Repair / Retry
```

### 핵심 레이어
- **vllm_orchestrator/** - 도메인 라우팅, 태스크 디스패치, circuit breaker, queue
- **runtime_llm_gateway/** - vLLM HTTP 추상화, 스키마 검증, 프롬프트 관리
- **core/** - 공유 모듈 (intent parser, variant generator, critique ranker)
- **backend/** - FastAPI API 라우터

## GPU Setup (Production)

- **GPU**: NVIDIA RTX 5070 (12GB VRAM)
- **Model**: Qwen2.5-7B-Instruct-AWQ (4-bit quantized, ~5GB)
- **Engine**: vLLM 0.19+ with awq_marlin kernel
- **Framework**: PyTorch 2.10+ (CUDA 12.8, sm_120 Blackwell)

### 서버 시작
```powershell
.\start_llm_server.ps1   # PowerShell
start_llm_server.bat      # 더블클릭
```

### WSL 직접 실행
```bash
source ~/vllm-env/bin/activate
VLLM_FLASH_ATTN_VERSION=2 vllm serve /home/suzzi/models/Qwen2.5-7B-Instruct-AWQ \
  --host 0.0.0.0 --port 8000 --api-key internal-token \
  --gpu-memory-utilization 0.85 --max-model-len 4096 \
  --quantization awq_marlin --enable-request-id-headers
```

## Testing

```bash
# 기본 gate (549 tests, ~4min)
pytest vllm_orchestrator/tests/

# 실서버 연동 테스트 (vLLM 필요)
pytest vllm_orchestrator/tests/ -m infra

# 부하 테스트
pytest vllm_orchestrator/tests/ -m load

# 전체 verification suite
python -X utf8 -m runtime_llm_gateway.tests.verification_suite
```

## Config

- `runtime_llm_gateway/server_config.json` - 모델, timeout, 프로필 설정
- `vllm_orchestrator/src/app/settings.py` - 오케스트레이터 설정 (env override 가능)
- 환경변수: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `APP_ENV`, `QUEUE_CONCURRENCY`

## Fallback

GPU 불가 시 `fallback_server.py`로 CPU 모드 가능 (Qwen2.5-0.5B, 매우 느림).
프로덕션은 반드시 GPU vLLM 사용.
