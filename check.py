import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

env_file = find_dotenv(usecwd=True)
print("1. .env file found at:", env_file or "NOT FOUND")
load_dotenv(env_file)

key = os.getenv("GEMINI_API_KEY", "")
print("2. OFFLINE_MODE =", repr(os.getenv("OFFLINE_MODE")))
print("3. Key present:", bool(key.strip()), "| length:", len(key.strip()),
      "| starts with:", key.strip()[:4] or "-")
print("4. Model:", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

try:
    from google import genai
    client = genai.Client(api_key=key.strip())
    r = client.models.generate_content(model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                                       contents="Say hello in five words")
    print("5. SUCCESS:", r.text)
except Exception as e:
    print("5. ERROR:", type(e).__name__, "-", e)