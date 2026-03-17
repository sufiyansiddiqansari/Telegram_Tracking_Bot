import requests

# Test metaAndAssetCtxs
res = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}).json()
print("metaAndAssetCtxs keys:", len(res))

if isinstance(res, list) and len(res) == 2:
    meta = res[0]
    ctxs = res[1]
    print("Found ctxs for", len(ctxs), "assets")
    if len(ctxs) > 0:
        print("Sample ctx:", ctxs[0])

print("---")
# Test vaults exploring or leaderboards natively
try:
    res = requests.post("https://api.hyperliquid.xyz/info", json={"type": "explorerVitals"}).json()
    print("Explorer vitals:", type(res))
except:
    pass
    
try:
    res = requests.post("https://api.hyperliquid.xyz/info", json={"type": "openapi", "content": "vaults"}).json()
    print("Vaults test1:", type(res))
except:
    pass
