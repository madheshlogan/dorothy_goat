"""Quick check that Gemini is set up: python check_gemini.py"""
import os
from dotenv import load_dotenv, find_dotenv

env_file = find_dotenv(usecwd=True)
print("1. .env file found at:", env_file or "NOT FOUND")
load_dotenv(env_file)

key = os.getenv("GEMINI_API_KEY", "").strip()
model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
print("2. OFFLINE_MODE =", repr(os.getenv("OFFLINE_MODE")))
print("3. Key present:", bool(key), "| length:", len(key), "| starts with:", key[:4] or "-")
print("4. Model:", model)

try:
    from google import genai
    r = genai.Client(api_key=key).models.generate_content(model=model, contents="Say hello in five words")
    print("5. SUCCESS:", r.text)
except Exception as e:
    print("5. ERROR:", type(e).__name__, "-", e)
