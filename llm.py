"""Optional Gemini wrapper. Any failure safely falls back to deterministic demo behavior."""
import json, os
from dotenv import load_dotenv
load_dotenv()

def gemini_text(prompt: str):
    if os.getenv("OFFLINE_MODE", "true").lower() == "true":
        return None
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    try:
        from google import genai
        client = genai.Client(api_key=key)
        response = client.models.generate_content(model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"), contents=prompt)
        return getattr(response, "text", None)
    except Exception:
        return None

def safe_json(text):
    if not text: return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip('`')
        if text.lower().startswith('json'): text = text[4:]
    try: return json.loads(text)
    except Exception: return None
