"""
vision_server.py — 마인크래프트 빌드 비주얼 QA 서버

파이프라인:
  Florence-2-base  → 이미지 캡셔닝 (테마/실루엣/구조 요약)
  Grounding DINO   → 프롬프트 기반 구조물 영역 탐지 (tower, gate, roof edge 등)
  Qwen2.5-VL-3B   → 루브릭 기반 멀티모달 비평 (탐지 영역 + 루브릭 → 비평)

공통:
  PEFT/LoRA 어댑터 지원 (safetensors, 핫 스왑 가능)

실행: cd /d/LLM/vision_server && python server.py
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image, ImageDraw
from pydantic import BaseModel

from prompts import CAPTION_TASK, GROUNDING_PROMPTS, build_critique_prompt

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vision")

app = FastAPI(title="Minecraft Vision QA Server", version="2.0")

# ─── Model State ─────────────────────────────────────────────────────

_florence_model = None
_florence_processor = None
_gdino_model = None
_gdino_processor = None
_qwen_model = None
_qwen_processor = None
_device = "cuda" if torch.cuda.is_available() else "cpu"

# PEFT adapter registry: { adapter_name: path_to_safetensors }
_peft_adapters: dict[str, str] = {}
_active_adapter: Optional[str] = None


# ─── Model Loaders ───────────────────────────────────────────────────

def _load_florence():
    """Florence-2-base (~0.5GB VRAM)."""
    global _florence_model, _florence_processor
    if _florence_model is not None:
        return

    from transformers import AutoModelForCausalLM, AutoProcessor

    model_id = os.getenv("FLORENCE_MODEL", "microsoft/Florence-2-base")
    log.info(f"Loading Florence-2 from {model_id}...")
    _florence_processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    _florence_model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=torch.float16, trust_remote_code=True,
    ).to(_device).eval()
    log.info("Florence-2 loaded.")


def _load_gdino():
    """Grounding DINO (~1.2GB VRAM)."""
    global _gdino_model, _gdino_processor
    if _gdino_model is not None:
        return

    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    model_id = os.getenv("GDINO_MODEL", "IDEA-Research/grounding-dino-base")
    log.info(f"Loading Grounding DINO from {model_id}...")
    _gdino_processor = AutoProcessor.from_pretrained(model_id)
    _gdino_model = AutoModelForZeroShotObjectDetection.from_pretrained(
        model_id, torch_dtype=torch.float16,
    ).to(_device).eval()
    log.info("Grounding DINO loaded.")


def _load_qwen_vl():
    """Qwen2.5-VL-3B-Instruct-AWQ (~2GB VRAM)."""
    global _qwen_model, _qwen_processor
    if _qwen_model is not None:
        return

    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor

    model_id = os.getenv("QWEN_VL_MODEL", "Qwen/Qwen2.5-VL-3B-Instruct-AWQ")
    log.info(f"Loading Qwen2.5-VL from {model_id}...")
    _qwen_processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    _qwen_model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_id, torch_dtype=torch.float16, device_map="auto", trust_remote_code=True,
    ).eval()
    log.info("Qwen2.5-VL loaded.")


# ─── PEFT / LoRA ─────────────────────────────────────────────────────

def _load_peft_adapter(adapter_name: str):
    """PEFT LoRA 어댑터 핫 스왑 (safetensors 기반)."""
    global _active_adapter
    if _qwen_model is None:
        _load_qwen_vl()

    adapter_path = _peft_adapters.get(adapter_name)
    if not adapter_path:
        raise ValueError(f"Unknown adapter: {adapter_name}")

    if _active_adapter == adapter_name:
        return  # 이미 로드됨

    try:
        from peft import PeftModel
        log.info(f"Loading PEFT adapter '{adapter_name}' from {adapter_path}...")
        # 기존 어댑터 해제
        if _active_adapter and hasattr(_qwen_model, 'disable_adapter'):
            _qwen_model.disable_adapter()

        if hasattr(_qwen_model, 'load_adapter'):
            _qwen_model.load_adapter(adapter_path, adapter_name=adapter_name)
            _qwen_model.set_adapter(adapter_name)
        else:
            # 최초 PEFT 래핑
            global _qwen_model  # noqa: F811
            _qwen_model = PeftModel.from_pretrained(_qwen_model, adapter_path, adapter_name=adapter_name)
            _qwen_model.eval()

        _active_adapter = adapter_name
        log.info(f"PEFT adapter '{adapter_name}' active.")
    except ImportError:
        log.warning("peft 패키지 미설치 — 어댑터 로딩 건너뜀")
    except Exception as e:
        log.error(f"PEFT adapter load failed: {e}")


# ─── Helpers ─────────────────────────────────────────────────────────

def _decode_image(image_base64: str) -> Image.Image:
    data = base64.b64decode(image_base64)
    return Image.open(io.BytesIO(data)).convert("RGB")


def _encode_image(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ─── Request / Response Models ───────────────────────────────────────

class CaptionRequest(BaseModel):
    image_base64: str

class CaptionResponse(BaseModel):
    caption: str
    latency_ms: int


class GroundRequest(BaseModel):
    image_base64: str
    prompts: list[str] = []       # ["tower", "roof edge", "window row", "gate"]
    threshold: float = 0.25

class DetectedRegion(BaseModel):
    label: str
    score: float
    bbox: list[float]             # [x1, y1, x2, y2] normalized 0-1

class GroundResponse(BaseModel):
    regions: list[DetectedRegion]
    annotated_image_base64: str = ""  # bbox가 그려진 이미지
    latency_ms: int = 0


class CritiqueRequest(BaseModel):
    image_base64: str
    user_intent: str = ""
    rubric_summary: str = ""
    detected_regions: list[DetectedRegion] = []
    adapter_name: str = ""        # PEFT 어댑터 지정 (빈 문자열 = 기본)

class CritiqueResponse(BaseModel):
    theme_match: float = 0.5
    silhouette_quality: float = 0.5
    weak_points: list[str] = []
    repair_suggestions: list[str] = []
    caption: str = ""
    critique: str = ""
    latency_ms: int = 0


class PipelineRequest(BaseModel):
    """전체 파이프라인: Florence-2 → Grounding DINO → Qwen2.5-VL 한 번에."""
    image_base64: str
    user_intent: str = ""
    rubric_summary: str = ""
    ground_prompts: list[str] = []
    ground_threshold: float = 0.25
    adapter_name: str = ""

class PipelineResponse(BaseModel):
    caption: str = ""
    regions: list[DetectedRegion] = []
    theme_match: float = 0.5
    silhouette_quality: float = 0.5
    weak_points: list[str] = []
    repair_suggestions: list[str] = []
    critique: str = ""
    total_latency_ms: int = 0


class AdapterRegisterRequest(BaseModel):
    name: str
    path: str  # safetensors 디렉토리 경로


# ─── Endpoints ───────────────────────────────────────────────────────

@app.post("/vision/caption", response_model=CaptionResponse)
def caption_build(req: CaptionRequest):
    """Florence-2: 이미지 → 테마/실루엣/구조 요약."""
    _load_florence()
    t0 = time.perf_counter()

    image = _decode_image(req.image_base64)
    inputs = _florence_processor(text=CAPTION_TASK, images=image, return_tensors="pt").to(_device)

    with torch.no_grad():
        generated = _florence_model.generate(**inputs, max_new_tokens=128, num_beams=3)
    text = _florence_processor.batch_decode(generated, skip_special_tokens=True)[0]
    caption = text.replace(CAPTION_TASK, "").strip()

    return CaptionResponse(caption=caption, latency_ms=round((time.perf_counter() - t0) * 1000))


@app.post("/vision/ground", response_model=GroundResponse)
def ground_regions(req: GroundRequest):
    """Grounding DINO: 프롬프트 기반 구조물 영역 탐지."""
    _load_gdino()
    t0 = time.perf_counter()

    image = _decode_image(req.image_base64)
    w, h = image.size

    # 프롬프트가 비어있으면 기본 마인크래프트 구조 프롬프트 사용
    prompts = req.prompts if req.prompts else GROUNDING_PROMPTS
    text_prompt = ". ".join(prompts) + "."

    inputs = _gdino_processor(images=image, text=text_prompt, return_tensors="pt").to(_device)

    with torch.no_grad():
        outputs = _gdino_model(**inputs)

    results = _gdino_processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        threshold=req.threshold,
        text_threshold=req.threshold,
        target_sizes=[(h, w)],
    )[0]

    regions: list[DetectedRegion] = []
    for score, label, bbox in zip(results["scores"], results["labels"], results["boxes"]):
        x1, y1, x2, y2 = bbox.tolist()
        regions.append(DetectedRegion(
            label=label,
            score=round(float(score), 3),
            bbox=[round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)],
        ))

    # annotated image 생성
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    colors = ["#FF4444", "#44FF44", "#4444FF", "#FFFF44", "#FF44FF", "#44FFFF"]
    for i, region in enumerate(regions):
        bx1, by1, bx2, by2 = region.bbox
        color = colors[i % len(colors)]
        draw.rectangle([bx1 * w, by1 * h, bx2 * w, by2 * h], outline=color, width=2)
        draw.text((bx1 * w + 2, by1 * h + 2), f"{region.label} {region.score:.2f}", fill=color)

    return GroundResponse(
        regions=regions,
        annotated_image_base64=_encode_image(annotated),
        latency_ms=round((time.perf_counter() - t0) * 1000),
    )


@app.post("/vision/critique", response_model=CritiqueResponse)
def critique_build(req: CritiqueRequest):
    """Qwen2.5-VL: 이미지 + 탐지 영역 + 루브릭 → 비평 JSON."""
    _load_qwen_vl()

    # PEFT 어댑터 적용 (지정된 경우)
    if req.adapter_name and req.adapter_name in _peft_adapters:
        _load_peft_adapter(req.adapter_name)

    t0 = time.perf_counter()

    image = _decode_image(req.image_base64)

    # 탐지 영역 정보를 프롬프트에 추가
    region_info = ""
    if req.detected_regions:
        region_lines = [f"- {r.label} (confidence {r.score:.2f}) at bbox [{r.bbox[0]:.2f},{r.bbox[1]:.2f},{r.bbox[2]:.2f},{r.bbox[3]:.2f}]"
                        for r in req.detected_regions]
        region_info = "\n## Detected Structural Regions (from Grounding DINO)\n" + "\n".join(region_lines)

    prompt = build_critique_prompt(req.user_intent, req.rubric_summary, region_info)

    messages = [
        {"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": prompt},
        ]}
    ]

    text_input = _qwen_processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = _qwen_processor(
        text=[text_input], images=[image], return_tensors="pt", padding=True,
    ).to(_qwen_model.device)

    with torch.no_grad():
        generated = _qwen_model.generate(
            **inputs, max_new_tokens=512, temperature=0.3, do_sample=True,
        )

    output_ids = generated[:, inputs.input_ids.shape[1]:]
    raw_text = _qwen_processor.batch_decode(output_ids, skip_special_tokens=True)[0].strip()
    latency = round((time.perf_counter() - t0) * 1000)

    try:
        json_str = raw_text
        if "```" in json_str:
            json_str = json_str.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
        data = json.loads(json_str.strip())
        return CritiqueResponse(
            theme_match=float(data.get("theme_match", 0.5)),
            silhouette_quality=float(data.get("silhouette_quality", 0.5)),
            weak_points=data.get("weak_points", []),
            repair_suggestions=data.get("repair_suggestions", []),
            caption=data.get("caption", ""),
            critique=data.get("critique", ""),
            latency_ms=latency,
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        log.warning(f"Failed to parse critique JSON: {raw_text[:200]}")
        return CritiqueResponse(critique=raw_text, latency_ms=latency)


@app.post("/vision/pipeline", response_model=PipelineResponse)
def full_pipeline(req: PipelineRequest):
    """전체 파이프라인: Florence-2 → Grounding DINO → Qwen2.5-VL."""
    t0 = time.perf_counter()

    # 1. Florence-2 캡션
    cap_resp = caption_build(CaptionRequest(image_base64=req.image_base64))

    # 2. Grounding DINO 영역 탐지
    ground_prompts = req.ground_prompts if req.ground_prompts else GROUNDING_PROMPTS
    gnd_resp = ground_regions(GroundRequest(
        image_base64=req.image_base64,
        prompts=ground_prompts,
        threshold=req.ground_threshold,
    ))

    # 3. Qwen2.5-VL 비평 (캡션 + 탐지 영역 → 컨텍스트로 전달)
    enriched_rubric = f"Florence-2 Caption: {cap_resp.caption}\n\n{req.rubric_summary}"
    crit_resp = critique_build(CritiqueRequest(
        image_base64=req.image_base64,
        user_intent=req.user_intent,
        rubric_summary=enriched_rubric,
        detected_regions=gnd_resp.regions,
        adapter_name=req.adapter_name,
    ))

    return PipelineResponse(
        caption=cap_resp.caption,
        regions=gnd_resp.regions,
        theme_match=crit_resp.theme_match,
        silhouette_quality=crit_resp.silhouette_quality,
        weak_points=crit_resp.weak_points,
        repair_suggestions=crit_resp.repair_suggestions,
        critique=crit_resp.critique,
        total_latency_ms=round((time.perf_counter() - t0) * 1000),
    )


# ─── PEFT Adapter Management ────────────────────────────────────────

@app.post("/adapters/register")
def register_adapter(req: AdapterRegisterRequest):
    """PEFT LoRA 어댑터 등록 (safetensors 디렉토리)."""
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(404, f"Adapter path not found: {req.path}")
    _peft_adapters[req.name] = req.path
    log.info(f"Adapter registered: {req.name} → {req.path}")
    return {"status": "registered", "name": req.name, "path": req.path}


@app.get("/adapters/list")
def list_adapters():
    return {
        "adapters": _peft_adapters,
        "active": _active_adapter,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "florence_loaded": _florence_model is not None,
        "gdino_loaded": _gdino_model is not None,
        "qwen_vl_loaded": _qwen_model is not None,
        "active_adapter": _active_adapter,
        "registered_adapters": list(_peft_adapters.keys()),
        "device": _device,
    }


if __name__ == "__main__":
    port = int(os.getenv("VISION_PORT", "8200"))
    log.info(f"Starting vision server on port {port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port)
