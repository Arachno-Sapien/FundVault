"""
Receipt / screenshot data extraction with per-org provider fallback.

Each organisation brings its own provider credentials (`Org.ai_config`,
encrypted at rest — see `apps.ledger.storage` for the parallel pattern with
storage credentials). A primary provider is tried first; on any failure the
optional fallback is tried next. Both are either an OpenAI-compatible
endpoint (base URL + model + key — NVIDIA NIM, or anything else that speaks
the same API) or Gemini, which uses its own SDK. The response includes a
`_provider` key so the frontend can show which model was used.
"""

import base64
import io
import json
import re
from dataclasses import dataclass, field

from PIL import Image

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


@dataclass
class AIConfig:
    provider: str          # "openai_compatible" | "gemini"
    model: str
    # repr=False: a stray `logger.info(config)` or unhandled-exception
    # traceback must not print this into a log line (same pattern as
    # apps.ledger.storage.StorageConfig).
    api_key: str = field(repr=False)
    base_url: str = ""


def _one(entry):
    if not isinstance(entry, dict):
        return None
    provider = entry.get("provider")
    model = entry.get("model")
    api_key = entry.get("api_key")
    if not model or not api_key:
        return None
    if provider == "openai_compatible":
        if not entry.get("base_url"):
            return None
        return AIConfig("openai_compatible", model, api_key, entry["base_url"])
    if provider == "gemini":
        return AIConfig("gemini", model, api_key)
    return None


def parse_ai_config(raw):
    """{'primary': AIConfig|None, 'fallback': AIConfig|None} from stored JSON."""
    if not raw:
        return {"primary": None, "fallback": None}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"primary": None, "fallback": None}
    if not isinstance(data, dict):  # e.g. a PUT of {"ai": null}
        return {"primary": None, "fallback": None}
    return {"primary": _one(data.get("primary")), "fallback": _one(data.get("fallback"))}


def _redact(message, config):
    """Strip the credential value out of a message before it can reach a log or API response."""
    if config.api_key:
        message = message.replace(config.api_key, "***")
    return message


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
# Provider: any OpenAI-compatible endpoint (NVIDIA NIM, etc.)
# ---------------------------------------------------------------------------

def _extract_openai_compatible(compressed: bytes, config: AIConfig) -> dict:
    from openai import OpenAI

    b64 = base64.b64encode(compressed).decode("utf-8")
    client = OpenAI(base_url=config.base_url, api_key=config.api_key)

    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        temperature=0.1,
        top_p=0.95,
        max_tokens=2048,
        stream=False,
    )
    choice = response.choices[0]
    raw = (choice.message.content or "").strip()
    if not raw:
        raw = (getattr(choice.message, "reasoning_content", None) or "").strip()
    if not raw:
        raise json.JSONDecodeError("Empty response from model", "", 0)

    result = _parse_json_from_text(raw)
    result["_provider"] = config.model
    return result


# ---------------------------------------------------------------------------
# Provider: Google Gemini
# ---------------------------------------------------------------------------

def _extract_gemini(compressed: bytes, config: AIConfig) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.api_key)
    response = client.models.generate_content(
        model=config.model,
        contents=[
            types.Content(
                parts=[
                    types.Part.from_text(text=_PROMPT),
                    types.Part.from_bytes(data=compressed, mime_type="image/jpeg"),
                ]
            )
        ],
    )
    result = _parse_json_from_text(response.text.strip())
    result["_provider"] = config.model
    return result


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def _run(config, compressed):
    if config.provider == "gemini":
        return _extract_gemini(compressed, config)
    return _extract_openai_compatible(compressed, config)


def extract_from_receipt_image(image_bytes, mime_type, config):
    """Extract payment details using the org's own providers.

    `config` is the dict returned by parse_ai_config. The primary is tried
    first; any failure falls through to the fallback if one is configured.
    """
    primary = config.get("primary")
    fallback = config.get("fallback")
    if not primary and not fallback:
        return {
            "error": "Receipt extraction is not configured for this organisation. "
                     "An Owner can add an AI provider in organisation settings."
        }

    try:
        compressed = _compress_image(image_bytes)
    except Exception as exc:
        return {"error": f"Image processing failed: {exc}"}

    errors = []
    for label, candidate in (("primary", primary), ("fallback", fallback)):
        if not candidate:
            continue
        try:
            return _run(candidate, compressed)
        except json.JSONDecodeError:
            errors.append(f"{label} ({candidate.model}): could not parse the model response")
        except Exception as exc:
            errors.append(f"{label} ({candidate.model}): {_redact(str(exc), candidate)}")

    return {"error": "Extraction failed. " + " | ".join(errors)}


def check_ai_config(config):
    """Probe a provider cheaply so a bad key surfaces in settings, not at first use.

    Also the write-time SSRF gate for `base_url` (see check_storage in
    apps.ledger.storage for the same reasoning). Gemini needs no check: its
    endpoint is fixed by the SDK, not supplied by the org.
    """
    target = config.get("primary") or config.get("fallback")
    if not target:
        return False, "No provider configured."

    from apps.orgs.provisioning import blocked_https_url_message

    # Both slots, not just the one probed below: the fallback is persisted by
    # the same request and dialled later, at extraction time.
    for candidate in (config.get("primary"), config.get("fallback")):
        if candidate is None or candidate.provider != "openai_compatible":
            continue
        blocked = blocked_https_url_message(candidate.base_url)
        if blocked:
            return False, blocked

    try:
        if target.provider == "gemini":
            from google import genai

            genai.Client(api_key=target.api_key).models.list()
        else:
            from openai import OpenAI

            OpenAI(base_url=target.base_url, api_key=target.api_key).models.list()
        return True, f"{target.model} is reachable."
    except Exception as exc:
        return False, _redact(str(exc), target)
