"""Gemini wrapper. Falls back to offline mode on any failure, and prints why in the terminal."""
import json
import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "gemini-3.5-flash-lite"  # current free-tier model with minimal thinking by default
_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", "").strip())
    return _client


def gemini_text(prompt: str, system: str = None, max_tokens: int = 1024):
    """Return Gemini's text reply, or None if offline / not configured / the call failed."""
    if os.getenv("OFFLINE_MODE", "true").strip().lower() == "true":
        return None
    if not os.getenv("GEMINI_API_KEY", "").strip():
        print("[gemini] No GEMINI_API_KEY set - using offline mode")
        return None
    try:
        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.8,
            # Note: on Gemini 3.x models any "thinking" also counts toward this limit.
            max_output_tokens=max_tokens,
        )
        response = _get_client().models.generate_content(
            model=os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip(),
            contents=prompt,
            config=config,
        )
        text = getattr(response, "text", None)
        if not text:
            print("[gemini] empty reply - using offline mode for this turn")
        return text
    except Exception as e:
        print(f"[gemini] call failed, using offline mode: {type(e).__name__}: {e}")
        return None


def safe_json(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except Exception:
        return None
