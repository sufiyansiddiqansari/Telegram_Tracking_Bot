import requests

try:
    res = requests.get("https://hyperdash.com/api/explore/global")
    print(res.status_code)
    print(res.text[:200])
except Exception as e:
    print(e)
    
try:
    res = requests.post("https://api.hyperdash.info/explore/global")
    print("API2", res.status_code)
except Exception as e:
    pass

try:
    headers = {'User-Agent': 'Mozilla/5.0'}
    res = requests.get("https://hyperdash.info/api/traders?limit=10&sortBy=pnl", headers=headers)
    print("API3", res.status_code)
except Exception as e:
    pass
