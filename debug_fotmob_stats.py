#!/usr/bin/env python
# debug_fotmob_stats.py — find where stats are in Fotmob page
# python debug_fotmob_stats.py

import hashlib, hmac, time, base64, json, re, cloudscraper

def get_token():
    secret = b"cYmAYABmAzGGGGGA"
    ts = int(time.time() * 1000)
    sig = hmac.new(secret, str(ts).encode(), hashlib.sha256).hexdigest()
    return base64.b64encode(json.dumps({"token": sig, "expiration": ts}).encode()).decode()

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)
scraper.headers.update({
    "x-mas": get_token(),
    "Referer": "https://www.fotmob.com/",
    "Accept": "text/html,application/xhtml+xml",
})

BASE = "https://www.fotmob.com"

# Test with Hakimi (verified ID)
HAKIMI_ID = 770881

print(f"=== Fetching /api/players/{HAKIMI_ID} (Hakimi) ===")
r = scraper.get(f"{BASE}/api/players/{HAKIMI_ID}", timeout=15)
print(f"Status: {r.status_code}, Size: {len(r.text)} chars")

if r.status_code == 200:
    html = r.text

    # Extract __NEXT_DATA__
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if match:
        data = json.loads(match.group(1))

        # Save full JSON
        with open("fotmob_hakimi_full.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("✅ Saved fotmob_hakimi_full.json")

        # Navigate structure
        props = data.get("props", {}).get("pageProps", {})
        main_data = props.get("data", {})

        print(f"\n=== pageProps.data keys: {list(main_data.keys())} ===")

        # Check all nested keys
        def print_keys(d, prefix="", depth=0):
            if depth > 4: return
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, (dict, list)):
                        size = len(v) if isinstance(v, list) else len(v.keys())
                        print(f"{'  '*depth}{prefix}{k}: ({type(v).__name__}, {size})")
                        if isinstance(v, dict):
                            print_keys(v, prefix="", depth=depth+1)
                        elif isinstance(v, list) and v and isinstance(v[0], dict):
                            print_keys(v[0], prefix="[0].", depth=depth+1)
                    else:
                        print(f"{'  '*depth}{prefix}{k}: {repr(v)[:60]}")

        print_keys(main_data)

        # Search for stats-related keys anywhere in the data
        print("\n=== Searching for stats/goals/tackles anywhere ===")
        full_str = json.dumps(data)
        for keyword in ["goals", "tackles", "interceptions", "clearances", "assists", "statSection", "career", "season"]:
            count = full_str.lower().count(keyword.lower())
            if count > 0:
                # Find context
                idx = full_str.lower().find(keyword.lower())
                print(f"  '{keyword}': {count} occurrences | first context: ...{full_str[max(0,idx-30):idx+50]}...")

print("\n\n=== Also trying /api/players/{id}/stats endpoint ===")
stats_endpoints = [
    f"/api/players/{HAKIMI_ID}/stats",
    f"/api/players/{HAKIMI_ID}/career",
    f"/api/players/{HAKIMI_ID}/season",
    f"/api/playerseasonStats?playerId={HAKIMI_ID}",
    f"/api/playerCareerStats?playerId={HAKIMI_ID}",
    f"/api/playerStats?playerId={HAKIMI_ID}&seasonId=2025",
]

scraper.headers.update({"Accept": "application/json, text/plain, */*"})
for ep in stats_endpoints:
    r2 = scraper.get(BASE + ep, timeout=8)
    print(f"  [{r2.status_code}] {ep}")
    if r2.status_code == 200:
        try:
            d = r2.json()
            print(f"    ✅ JSON keys: {list(d.keys())[:8]}")
            with open(f"fotmob_stats_{ep.split('/')[-1][:20]}.json", "w") as f:
                json.dump(d, f, indent=2)
        except:
            print(f"    HTML: {len(r2.text)} chars")
