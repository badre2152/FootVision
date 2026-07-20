#!/usr/bin/env python
# debug_fotmob_nextdata.py
# يستخرج __NEXT_DATA__ من /api/players/{id}
# ويكشف الـ structure ديال الـ stats
# python debug_fotmob_nextdata.py

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

BASE = "https://www.fotmob.com"

# ── Step 1: Search for Hakimi to get correct ID ──────────────────────────────
print("=== STEP 1: Search for correct player IDs ===")
search_players = [
    ("Achraf Hakimi",   "achraf-hakimi"),
    ("Sofyan Amrabat",  "sofyan-amrabat"),
    ("Brahim Diaz",     "brahim-diaz"),
    ("Youssef En-Nesyri","youssef-en-nesyri"),
]

scraper.headers.update({"Accept": "text/html,application/xhtml+xml"})

for name, slug in search_players:
    # Try different ID ranges — search by slug
    # Fotmob player pages: fotmob.com/players/{id}/{slug}
    # We need to find the ID — try fetching search page
    url = f"{BASE}/search?term={name.replace(' ', '+')}"
    try:
        r = scraper.get(url, timeout=10)
        if r.status_code == 200:
            # Extract player IDs from HTML
            ids = re.findall(r'/players/(\d+)/' + slug.split('-')[0], r.text)
            ids_general = re.findall(r'"id":\s*(\d{5,7})', r.text[:5000])
            print(f"  {name}: found IDs in search page: {ids[:5]}")
            print(f"  General IDs in page: {ids_general[:10]}")
    except Exception as e:
        print(f"  {name}: {e}")

# ── Step 2: Extract __NEXT_DATA__ from /api/players/961995 (Saka, to see structure)
print("\n=== STEP 2: Extract __NEXT_DATA__ structure from player page ===")
r = scraper.get(f"{BASE}/api/players/961995", timeout=15)
if r.status_code == 200:
    html = r.text
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if match:
        data = json.loads(match.group(1))
        # Save full structure
        with open("fotmob_nextdata_sample.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("  ✅ __NEXT_DATA__ found and saved to fotmob_nextdata_sample.json")

        # Show top-level structure
        props = data.get("props", {}).get("pageProps", {})
        print(f"  pageProps keys: {list(props.keys())}")

        player = props.get("player", props.get("playerProfile", props.get("data", {})))
        print(f"  player keys: {list(player.keys())[:10] if isinstance(player, dict) else 'not dict'}")

        # Find stats
        for key in ["stats", "playerStats", "career", "careerStatistics", "statSections"]:
            val = player.get(key) if isinstance(player, dict) else None
            if val:
                print(f"  Found '{key}': {str(val)[:200]}")
    else:
        print("  ❌ No __NEXT_DATA__ found")
        # Try to find any JSON with player data
        json_blocks = re.findall(r'\{[^{}]*"goals"[^{}]*\}', html)
        print(f"  JSON blocks with 'goals': {json_blocks[:3]}")

# ── Step 3: Try the correct Hakimi page directly  
print("\n=== STEP 3: Try fotmob.com/players/{id}/{slug} for Hakimi ===")
# Hakimi's actual Fotmob ID — from the URL bar in the browser screenshot
# The screenshot shows fotmob.com/fr/players/961995/achraf-hakimi
# But page shows Bukayo Saka → ID 961995 is Saka
# Hakimi's real ID needs to be found

# Try searching via the page itself
test_ids_for_hakimi = [814594, 796794, 892712, 798643, 812345]
for tid in test_ids_for_hakimi:
    url = f"{BASE}/api/players/{tid}"
    r2 = scraper.get(url, timeout=8)
    if r2.status_code == 200:
        # Extract player name
        name_match = re.search(r'"name":"([^"]+)"', r2.text[:2000])
        nm = name_match.group(1) if name_match else "?"
        print(f"  ID {tid}: {nm} [{r2.status_code}]")
    else:
        print(f"  ID {tid}: [{r2.status_code}]")
