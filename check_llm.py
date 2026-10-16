import os
import requests
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("LLM_API_KEY", "")
headers = {"x-goog-api-key": key}
base = "https://generativelanguage.googleapis.com/v1beta"

r = requests.get(f"{base}/models", headers=headers, timeout=20)
print("list models status:", r.status_code)
if r.status_code != 200:
    print(r.text[:300])
    raise SystemExit(1)

models = [m["name"] for m in r.json().get("models", [])
          if "generateContent" in m.get("supportedGenerationMethods", [])]
print("models that support generateContent:")
for m in models:
    print(" ", m)

body = {"contents": [{"parts": [{"text": "Reply with the single word OK."}]}]}
for name in ["models/gemini-3.8-flash", "models/gemini-3.5-flash-lite"]:
    if name not in models:
        print(name, "not in the list, skipped")
        continue
    r2 = requests.post(f"{base}/{name}:generateContent",
                       headers=headers, json=body, timeout=30)
    print("\nmodel:", name, "| status:", r2.status_code)
    print(r2.text[:400])