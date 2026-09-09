"""
Receipt / screenshot data extraction with automatic provider fallback.

Primary:   NVIDIA Nemotron 3 Nano Omni  (nvidia/nemotron-3-nano-omni-30b-a3b-reasoning)
Fallback:  Google Gemini 2.0 Flash      (gemini-2.0-flash)

If the primary provider fails for any reason (quota exhausted, network
error, bad JSON, etc.) the request is automatically retried with the
fallback provider.  The response includes a `_provider` key so the
frontend can show which model was used.
"""

import base64
import io
import json
import re

from PIL import Image
from django.conf import settings

# ---------------------------------------------------------------------------
# Shared prompt
# ---------------------------------------------------------------------------
_PROMPT = (
    "Extract payment details from this transaction screenshot or receipt image. "
    "Return ONLY a JSON object with these exact keys:\n"
    "- amount: number (e.g. 1500.00), null if not found\n"
    "- date: ISO8601 string (e.g. \"2026-06-19T10:30:00\"), null if not found\n"
    "- sender: string (name or account), null if not found\n"
    "- receiver: string (name or account), null if not found\n"
    "- reference_id: string (UPI ref, UTR, txn ID), null if not found\n"
    "- mode: one of \"electronic\", \"cheque\", \"cash\", null if not found\n"
    "- confidence: float 0.0-1.0\n"
    "No explanation. No markdown. No reasoning text. Raw JSON only."
)

# ---------------------------------------------------------------------------
# Mock response (dev / testing — zero API calls)
# ---------------------------------------------------------------------------
_MOCK_RESPONSE = {
    "amount": 1500.00,
    "date": "2026-06-19T10:30:00",
    "sender": "Test Sender",
    "receiver": "Test Receiver",
    "reference_id": "UPI123MOCK456",
    "mode": "electronic",
    "confidence": 0.95,
    "_mock": True,
    "_provider": "mock",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _compress_image(image_bytes: bytes) -> bytes:
    """Resize to max 1024 px, convert to JPEG 75 % — reduces payload ~95 %."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    if max(img.size) > 1024:
        img.thumbnail((1024, 1024), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    return buf.getvalue()


def _parse_json_from_text(text: str) -> dict:
    """
    Robustly extract a JSON object from model output that may contain:
    - <think>...</think> reasoning blocks (Nemotron)
    - Partial / unclosed <think> blocks
    - ```json ... ``` or ``` ... ``` markdown fences
    - Explanatory prose before or after the JSON

    Raises json.JSONDecodeError if no valid JSON object can be found.
    """
    if not text:
        raise json.JSONDecodeError("Empty response", "", 0)

    # 1. Remove complete <think>...</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # 2. Remove any remaining unclosed <think> block (model was cut off mid-think)
    #    Everything from an orphaned <think> to end-of-string is reasoning noise.
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)

    text = text.strip()

    # 3. Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # 4. Isolate the first complete JSON object { ... }
    #    Walk through to find balanced braces in case there's trailing text.
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)

    depth = 0
    end = -1
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end == -1:
        raise json.JSONDecodeError("Unbalanced JSON braces", text, start)

    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------------------
# Provider: NVIDIA Nemotron
# ---------------------------------------------------------------------------

def _extract_nvidia(compressed: bytes, api_key: str) -> dict:
    """
    Call NVIDIA NIM API with the compressed JPEG.
    Returns a parsed dict with _provider='nvidia' on success.
    Raises an exception on any failure so the caller can fall through.
    """
    from openai import OpenAI

    b64 = base64.b64encode(compressed).decode("utf-8")
    data_uri = f"data:image/jpeg;base64,{b64}"

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
    )

    response = client.chat.completions.create(
        model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        temperature=0.1,
        top_p=0.95,
        max_tokens=2048,   # needs room for reasoning + JSON output
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False,  # skip reasoning, emit JSON directly
            }
        },
        stream=False,
    )

    choice = response.choices[0]

    # The model sometimes puts the answer in reasoning_content instead of content
    raw = (choice.message.content or "").strip()
    if not raw:
        # Try the reasoning_content field (present on some NIM responses)
        reasoning = getattr(choice.message, "reasoning_content", None)
        if reasoning:
            raw = reasoning.strip()

    if not raw:
        raise json.JSONDecodeError("Empty response from NVIDIA model", "", 0)

    result = _parse_json_from_text(raw)
    result["_provider"] = "nvidia"
    return result


# ---------------------------------------------------------------------------
# Provider: Google Gemini
# ---------------------------------------------------------------------------

def _extract_gemini(compressed: bytes, api_key: str) -> dict:
    """
    Call Gemini 2.0 Flash with the compressed JPEG.
    Returns a parsed dict with _provider='gemini' on success.
    Raises an exception on any failure so the caller can fall through.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            types.Content(
                parts=[
                    types.Part.from_text(text=_PROMPT),
                    types.Part.from_bytes(data=compressed, mime_type="image/jpeg"),
                ]
            )
        ],
    )

    raw = response.text.strip()
    result = _parse_json_from_text(raw)
    result["_provider"] = "gemini"
    return result


# ---------------------------------------------------------------------------
# Public entry point — fallback chain
# ---------------------------------------------------------------------------

def extract_from_receipt_image(image_bytes: bytes, mime_type: str) -> dict:
    """
    Extract payment details from a receipt / screenshot image.

    Tries NVIDIA first, then falls back to Gemini automatically.
    Returns a dict with extracted fields (plus '_provider' indicating
    which model succeeded) or {"error": "..."} if both fail.
    """
    # ── Mock mode ──────────────────────────────────────────────────────
    if getattr(settings, "NVIDIA_RECEIPT_MOCK", False) or getattr(settings, "GEMINI_RECEIPT_MOCK", False):
        return dict(_MOCK_RESPONSE)

    # ── Compress once — reused by both providers ───────────────────────
    try:
        compressed = _compress_image(image_bytes)
    except Exception as exc:
        return {"error": f"Image processing failed: {exc}"}

    nvidia_key = getattr(settings, "NVIDIA_API_KEY", "")
    gemini_key = getattr(settings, "GEMINI_API_KEY", "")

    errors = []

    # ── 1. Try NVIDIA ──────────────────────────────────────────────────
    if nvidia_key:
        try:
            return _extract_nvidia(compressed, nvidia_key)
        except json.JSONDecodeError:
            errors.append("NVIDIA: could not parse model response")
        except Exception as exc:
            errors.append(f"NVIDIA: {exc}")
    else:
        errors.append("NVIDIA: no API key configured")

    # ── 2. Fall back to Gemini ─────────────────────────────────────────
    if gemini_key:
        try:
            return _extract_gemini(compressed, gemini_key)
        except json.JSONDecodeError:
            errors.append("Gemini: could not parse model response")
        except Exception as exc:
            errors.append(f"Gemini: {exc}")
    else:
        errors.append("Gemini: no API key configured")

    # ── Both failed ────────────────────────────────────────────────────
    return {"error": "Both providers failed: " + " | ".join(errors)}
