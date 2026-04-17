"""
fallback_server.py - CPU 전용 OpenAI 호환 폴백 서버 (비상용)

GPU vLLM이 사용 불가할 때만 사용하는 경량 폴백.
프로덕션은 vLLM serve (GPU, Qwen2.5-7B-Instruct-AWQ) 사용.

WSL Ubuntu에서 실행:
  source ~/vllm-env/bin/activate
  MODEL_PATH=/home/suzzi/models/qwen2.5-0.5b-instruct python3 fallback_server.py

Windows에서 접근: http://localhost:8000
"""

import json
import time
import os
from typing import Optional

# 모델 경로 (0.5B = CPU에서도 가벼움, ~1GB RAM)
MODEL_PATH = os.environ.get("MODEL_PATH", "/home/suzzi/models/qwen2.5-0.5b-instruct")
API_KEY = "internal-token"
PORT = 8000


def load_model():
    """transformers 모델 로드"""
    from transformers import AutoTokenizer, AutoModelForCausalLM
    import torch

    print(f"[INFO] Loading model from {MODEL_PATH}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH,
        dtype=torch.float32,
        device_map="cpu",
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    print(f"[INFO] Model loaded successfully")
    return tokenizer, model


def generate(tokenizer, model, messages: list, max_tokens: int = 512, temperature: float = 0.1) -> str:
    """채팅 생성"""
    import torch

    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt")

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            temperature=max(temperature, 0.01),
            do_sample=temperature > 0,
            pad_token_id=tokenizer.eos_token_id,
        )

    response = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return response.strip()


def create_app(tokenizer, model):
    """FastAPI OpenAI 호환 앱"""
    from fastapi import FastAPI, Header, HTTPException
    from pydantic import BaseModel

    app = FastAPI(title="Fallback LLM Server (OpenAI Compatible)")

    class ChatRequest(BaseModel):
        model: str = "qwen2.5-0.5b-instruct"
        messages: list
        max_tokens: int = 512
        temperature: float = 0.1

    @app.get("/v1/models")
    def list_models():
        return {"data": [{"id": os.path.basename(MODEL_PATH), "object": "model"}]}

    @app.post("/v1/chat/completions")
    def chat_completions(req: ChatRequest, authorization: Optional[str] = Header(None)):
        # API key 확인
        if authorization and authorization != f"Bearer {API_KEY}":
            raise HTTPException(401, "Invalid API key")

        start = time.time()
        text = generate(tokenizer, model, req.messages, req.max_tokens, req.temperature)
        latency = time.time() - start

        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "model": req.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    @app.get("/health")
    def health():
        return {"status": "ok", "model": MODEL_PATH}

    return app


if __name__ == "__main__":
    import uvicorn

    if not os.path.exists(MODEL_PATH):
        print(f"[ERROR] Model not found at {MODEL_PATH}")
        print(f"Download: huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct --local-dir {MODEL_PATH}")
        exit(1)

    tokenizer, model = load_model()
    app = create_app(tokenizer, model)
    print(f"[INFO] Server starting on 0.0.0.0:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
