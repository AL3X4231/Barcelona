import asyncio
import json
from camoufox.async_api import AsyncCamoufox

with open('results/latest_cart.json', 'r') as f:
    data = json.load(f)

proxy = data['proxy']
proxy_cfg = {
    'server': f"http://{proxy['host']}:{proxy['port']}",
    'username': proxy['username'],
    'password': proxy['password']
}

async def test():
    print('Testing AsyncCamoufox launch...')
    async with AsyncCamoufox(headless=True, proxy=proxy_cfg, geoip=True) as browser:
        print('Browser launched successfully!')

asyncio.run(test())
