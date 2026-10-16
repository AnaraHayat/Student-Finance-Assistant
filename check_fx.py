import requests

r = requests.get("https://open.er-api.com/v6/latest/CNY", timeout=15)
r.raise_for_status()
data = r.json()
print("updated:", data.get("time_last_update_utc"))
print("next update:", data.get("time_next_update_utc"))
print("1 CNY in PKR:", data["rates"]["PKR"])