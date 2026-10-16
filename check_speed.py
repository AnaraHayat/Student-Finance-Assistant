import os
import time

import requests
from dotenv import load_dotenv

from agent.llm import BASE
from agent.schemas import DECLARATIONS

load_dotenv()
key = os.getenv("LLM_API_KEY", "")
models = [os.getenv("LLM_MODEL", "gemini-3.8-flash"),
          os.getenv("LLM_FALLBACK_MODEL", "gemini-3.5-flash-lite")]
tools = [{"functionDeclarations": DECLARATIONS}]
system = ("Today's date is 2026-10-12. Record expenses with the "
          "add_expense tool.")
contents = [{"role": "user", "parts": [
    {"text": "Add expense: shopping 70 CNY"}]}]

for name in models:
    print("\n==", name)
    times = []
    for i in range(4):
        t0 = time.time()
        try:
            r = requests.post(
                f"{BASE}/{name}:generateContent",
                headers={"x-goog-api-key": key},
                json={"contents": contents, "tools": tools,
                      "systemInstruction": {"parts": [{"text": system}]}},
                timeout=(5, 40))
            secs = time.time() - t0
            parts = (r.json().get("candidates") or [{}])[0] \
                .get("content", {}).get("parts", [])
            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            what = (f"{calls[0]['name']} {calls[0].get('args')}"
                    if calls else "NO TOOL CALL")
            print(f"  try {i + 1}: HTTP {r.status_code} in {secs:.1f}s -> {what}")
            times.append(secs)
        except requests.RequestException as e:
            print(f"  try {i + 1}: {type(e).__name__} after "
                  f"{time.time() - t0:.1f}s")
    if times:
        times.sort()
        print(f"  fastest {times[0]:.1f}s, slowest {times[-1]:.1f}s")