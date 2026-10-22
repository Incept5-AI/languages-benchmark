import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:__PORT__/api/products") as r:
    data = json.loads(r.read().decode("utf-8"))
sel = sorted((p for p in data if float(p["price"]) > 50.00), key=lambda p: p["name"])
print("=== Filtered Products (price > $50.00) ===")
print()
for p in sel:
    print(f"{p['name']}: ${float(p['price']):.2f}")
print()
print(f"Count: {len(sel)}")
print(f"Total: ${sum(float(p['price']) for p in sel):.2f}")
