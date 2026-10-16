import re


class MockModel:
    """Offline stand-in used when no API key is set. It only understands
    one thing: an amount in the message, which it turns into a route
    comparison. Its job is to keep the demo and the tests runnable."""

    def generate(self, contents, tools, system):
        part = contents[-1]["parts"][0]

        if "functionResponse" in part:
            resp = part["functionResponse"]["response"]
            quotes = resp.get("quotes")
            if quotes:
                q = quotes[0]
                text = (
                    f"[Demo mode] Cheapest route: {q['route']}, total "
                    f"{q['total_pkr']} PKR (effective rate "
                    f"{q['effective_rate']}, hidden cost "
                    f"{q['hidden_cost_pkr']} PKR). Fee source: "
                    f"{q['fee_source']} (as of {q['fees_as_of']}).")
            elif "error" in resp:
                text = "[Demo mode] The tool returned an error: " + str(
                    resp["error"])
            else:
                text = "[Demo mode] Done."
            return [{"text": text}]

        message = part.get("text", "")
        m = re.search(r"\d+(?:\.\d+)?", message.replace(",", ""))
        if m:
            return [{"functionCall": {
                "name": "compare_routes",
                "args": {"cny_to_deliver": float(m.group())}}}]
        return [{"text": "[Demo mode] No AI key is configured, so I can only "
                         "compare routes when you give an amount, for example "
                         "'cheapest way to send 8000 CNY'."}]