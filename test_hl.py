import requests

print("Testing market...")
res = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}).json()
meta = res[0]
ctxs = res[1]

universe = meta.get("universe", [])
assets = []
for i, ctx in enumerate(ctxs):
    if i < len(universe):
        coin = universe[i]["name"]
        vol = float(ctx.get("dayNtlVlm", 0))
        px = float(ctx.get("markPx", 0))
        prev_px = float(ctx.get("prevDayPx", px))
        volatility = ((px - prev_px) / prev_px) * 100 if prev_px > 0 else 0
        assets.append({"coin": coin, "vol": vol, "volatility": volatility})

assets.sort(key=lambda x: x["vol"], reverse=True)
print(assets[:3])

print("Testing toptraders/vaults...")
try:
    res = requests.post("https://api.hyperliquid.xyz/info", json={"type": "vaults"}).json()
    print("Vaults test type payload length:", len(res) if isinstance(res, list) else "not list")
except Exception as e:
    print(e)
    
try:
    res = requests.post("https://api.hyperliquid.xyz/info", json={"type": "meta", "content": "vaults"}).json()
    print(res)
except Exception as e:
    pass

try:
    res = requests.post("https://api.hyperliquid.xyz/info", json={"type": "explorerVitals"}).json()
    print(res)
except Exception as e:
    pass
