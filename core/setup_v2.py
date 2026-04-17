"""
core/setup_v2.py — v2 로컬 LLM 셋업 스크립트

실행 순서:
1. python -m core.setup_v2 check     → 환경 확인
2. python -m core.setup_v2 download   → 모델 다운로드
3. python -m core.setup_v2 test       → LLM 연동 테스트

v1(규칙 기반)이 안정적으로 동작하는 것을 확인한 후 실행할 것.
v1 정확도 기준선: intent 98%, target 92% (eval_intent.py 기준)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "models")

RECOMMENDED_MODEL = "qwen2.5-7b"
MODELS = {
    "qwen2.5-7b": {
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "size_gb": 4.4,
        "vram_gb": 6,
        "korean": "excellent",
    },
    "qwen2.5-3b": {
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "file": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_gb": 1.9,
        "vram_gb": 3,
        "korean": "good",
    },
    "phi-3.5-mini": {
        "repo": "microsoft/Phi-3.5-mini-instruct-gguf",
        "file": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "size_gb": 2.2,
        "vram_gb": 4,
        "korean": "moderate",
    },
}


def check_environment():
    """환경 확인"""
    print("=" * 60)
    print("v2 Environment Check")
    print("=" * 60)

    # Python
    print(f"  Python: {sys.version}")

    # llama-cpp-python
    try:
        import llama_cpp
        print(f"  llama-cpp-python: {llama_cpp.__version__} [OK]")
    except ImportError:
        print("  llama-cpp-python: NOT INSTALLED")
        print("    Install: pip install llama-cpp-python")
        print("    GPU:     CMAKE_ARGS=\"-DGGML_CUDA=on\" pip install llama-cpp-python")

    # huggingface-hub
    try:
        import huggingface_hub
        print(f"  huggingface-hub: {huggingface_hub.__version__} [OK]")
    except ImportError:
        print("  huggingface-hub: NOT INSTALLED")
        print("    Install: pip install huggingface-hub")

    # 모델 파일
    print()
    print("  Models directory:", MODELS_DIR)
    os.makedirs(MODELS_DIR, exist_ok=True)
    for name, info in MODELS.items():
        path = os.path.join(MODELS_DIR, info["file"])
        exists = os.path.exists(path)
        marker = "[OK]" if exists else "[MISSING]"
        rec = " (recommended)" if name == RECOMMENDED_MODEL else ""
        print(f"    {name}{rec}: {marker}")
        if exists:
            size_mb = os.path.getsize(path) / (1024 * 1024)
            print(f"      Size: {size_mb:.0f} MB")

    # CUDA
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        if cuda_available:
            print(f"\n  CUDA: Available ({torch.cuda.get_device_name(0)})")
            print(f"  VRAM: {torch.cuda.get_device_properties(0).total_mem / 1e9:.1f} GB")
        else:
            print("\n  CUDA: Not available (CPU mode)")
    except ImportError:
        print("\n  PyTorch: Not installed (can't check CUDA)")
        print("  llama-cpp-python can still use CUDA directly")

    # core/ 모듈
    print()
    try:
        from core.schema_registry import SchemaRegistry
        from core.intent_parser import IntentParserModule
        from core.llm_backend import LocalLLMBackend
        print("  Core modules: All importable [OK]")
    except Exception as e:
        print(f"  Core modules: IMPORT ERROR - {e}")

    print("=" * 60)


def download_model(model_name: str = RECOMMENDED_MODEL):
    """모델 다운로드"""
    if model_name not in MODELS:
        print(f"Unknown model: {model_name}")
        print(f"Available: {', '.join(MODELS.keys())}")
        return

    info = MODELS[model_name]
    os.makedirs(MODELS_DIR, exist_ok=True)
    target = os.path.join(MODELS_DIR, info["file"])

    if os.path.exists(target):
        print(f"Model already exists: {target}")
        return

    print(f"Downloading {model_name} ({info['size_gb']} GB)...")
    print(f"  From: {info['repo']}")
    print(f"  File: {info['file']}")
    print(f"  To:   {MODELS_DIR}/")
    print()

    cmd = [
        sys.executable, "-m", "huggingface_hub", "download",
        info["repo"], info["file"],
        "--local-dir", MODELS_DIR,
    ]
    subprocess.run(cmd, check=True)
    print(f"\nDone! Model saved to: {target}")


def test_llm():
    """LLM 연동 테스트"""
    print("=" * 60)
    print("v2 LLM Integration Test")
    print("=" * 60)

    from core.llm_backend import LocalLLMBackend
    from core.schema_registry import SchemaRegistry
    from core.intent_parser import IntentParserModule
    from core.models import IntentType

    # 모델 찾기
    model_path = None
    for name, info in MODELS.items():
        path = os.path.join(MODELS_DIR, info["file"])
        if os.path.exists(path):
            model_path = path
            print(f"  Using model: {name} ({path})")
            break

    if not model_path:
        print("  ERROR: No model found. Run: python -m core.setup_v2 download")
        return

    # LLM 로드
    print("  Loading model...")
    llm = LocalLLMBackend(model_path=model_path)

    # Intent Parser에 연결
    registry = SchemaRegistry()
    parser = IntentParserModule(registry, "product_design")
    parser.llm_backend = llm

    # 테스트 케이스
    test_cases = [
        ("미니멀한 사무용 의자 만들어줘", "create_new"),
        ("전체 폭을 360mm로 바꿔줘", "modify_existing"),
        ("다른 안도 보여줘", "explore_variants"),
        ("두 번째 컨셉으로 갈게", "select"),
        ("이런 느낌으로 좀 바꿔", "modify_existing"),
    ]

    print()
    correct = 0
    for text, expected_type in test_cases:
        intent = parser.parse(text)
        actual = intent.intent_type.value
        match = actual == expected_type
        if match:
            correct += 1
        marker = "OK" if match else "FAIL"
        print(f"  [{marker}] \"{text}\"")
        print(f"    Expected: {expected_type} / Got: {actual}")
        print(f"    Target: {intent.target_object}, Confidence: {intent.confidence}")

    print()
    print(f"  Result: {correct}/{len(test_cases)} correct")
    print("=" * 60)


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m core.setup_v2 check     # Check environment")
        print("  python -m core.setup_v2 download   # Download model")
        print("  python -m core.setup_v2 test       # Test LLM integration")
        return

    command = sys.argv[1]
    if command == "check":
        check_environment()
    elif command == "download":
        model = sys.argv[2] if len(sys.argv) > 2 else RECOMMENDED_MODEL
        download_model(model)
    elif command == "test":
        test_llm()
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
