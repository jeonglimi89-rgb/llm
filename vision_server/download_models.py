"""
비전 모델 다운로드 스크립트

사용: python download_models.py [--all | --florence | --gdino | --qwen-vl]

모델 저장 위치: /d/LLM/models/vision/
"""
from __future__ import annotations

import argparse
import os
import sys

MODEL_DIR = os.getenv("VISION_MODEL_DIR", "/d/LLM/models/vision")

MODELS = {
    "florence": {
        "id": "microsoft/Florence-2-base",
        "size": "~0.5GB",
        "subdir": "florence-2-base",
    },
    "gdino": {
        "id": "IDEA-Research/grounding-dino-base",
        "size": "~1.2GB",
        "subdir": "grounding-dino-base",
    },
    "qwen-vl": {
        "id": "Qwen/Qwen2.5-VL-3B-Instruct-AWQ",
        "size": "~2GB",
        "subdir": "qwen2.5-vl-3b-awq",
    },
}


def download_model(key: str):
    from huggingface_hub import snapshot_download

    info = MODELS[key]
    dest = os.path.join(MODEL_DIR, info["subdir"])
    print(f"\n{'='*60}")
    print(f"Downloading: {info['id']} ({info['size']})")
    print(f"Destination: {dest}")
    print(f"{'='*60}\n")

    os.makedirs(dest, exist_ok=True)
    snapshot_download(
        repo_id=info["id"],
        local_dir=dest,
        local_dir_use_symlinks=False,
    )
    print(f"\n✓ {key} downloaded to {dest}")


def main():
    parser = argparse.ArgumentParser(description="Download vision models for Minecraft QA")
    parser.add_argument("--all", action="store_true", help="Download all 3 models")
    parser.add_argument("--florence", action="store_true", help="Download Florence-2-base")
    parser.add_argument("--gdino", action="store_true", help="Download Grounding DINO")
    parser.add_argument("--qwen-vl", action="store_true", help="Download Qwen2.5-VL-3B-AWQ")
    args = parser.parse_args()

    targets = []
    if args.all:
        targets = list(MODELS.keys())
    else:
        if args.florence: targets.append("florence")
        if args.gdino: targets.append("gdino")
        if args.qwen_vl: targets.append("qwen-vl")

    if not targets:
        print("Usage: python download_models.py [--all | --florence | --gdino | --qwen-vl]")
        print(f"\nModels will be saved to: {MODEL_DIR}")
        for k, v in MODELS.items():
            print(f"  --{k:10s}  {v['id']:45s}  {v['size']}")
        sys.exit(0)

    total_gb = sum(float(MODELS[t]["size"].strip("~GB")) for t in targets)
    print(f"Will download {len(targets)} model(s), estimated total: ~{total_gb:.1f}GB")
    print(f"Destination: {MODEL_DIR}\n")

    for t in targets:
        download_model(t)

    # 환경변수 힌트 출력
    print(f"\n{'='*60}")
    print("Done! Set these env vars before starting the vision server:")
    for t in targets:
        info = MODELS[t]
        dest = os.path.join(MODEL_DIR, info["subdir"])
        var = {"florence": "FLORENCE_MODEL", "gdino": "GDINO_MODEL", "qwen-vl": "QWEN_VL_MODEL"}[t]
        print(f"  export {var}={dest}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
