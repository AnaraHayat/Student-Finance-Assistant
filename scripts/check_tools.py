import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("LLM_API_KEY", "")
model = os.getenv("LLM_MODEL", "gemini-3.8-flash")
url = ("https://generativelanguage.googleapis.com/v1beta/models/"
       f"{model}:generateContent")
headers = {"x-goog-api-key": key}

tools = [{"functionDeclarations": [{
    "name": "compare_routes",
    "description": "Compare routes for delivering an amount of CNY.",
    "parameters": {
        "type": "OBJECT",
        "properties": {"cny_to_deliver": {"type": "NUMBER"}},
        "required": ["cny_to_deliver"],
    },
}]}]

contents = [{"role": "user", "parts": [
    {"text": "I need 8000 yuan for tuition. What is the cheapest way?"}]}]

r = requests.post(url, headers=headers, timeout=60,
                  json={"contents": contents, "tools": tools})
print("status:", r.status_code)
data = r.json()
if r.status_code != 200:
    print(json.dumps(data)[:500])
    raise SystemExit(1)

parts = data["candidates"][0]["content"]["parts"]
for p in parts:
    if "functionCall" in p:
        print("function call:", json.dumps(p["functionCall"]))
    elif "text" in p:
        print("text:", p["text"][:200])
    print("  has thoughtSignature:", "thoughtSignature" in p)

calls = [p for p in parts if "functionCall" in p]
if calls:
    contents.append({"role": "model", "parts": parts})
    contents.append({"role": "user", "parts": [{"functionResponse": {
        "name": calls[0]["functionCall"]["name"],
        "response": {"quotes": [{"route": "TEST", "total_pkr": "100.00"}]},
    }}]})
    r2 = requests.post(url, headers=headers, timeout=60,
                       json={"contents": contents, "tools": tools})
    print("\nsecond call status:", r2.status_code)
    print(json.dumps(r2.json())[:500])
