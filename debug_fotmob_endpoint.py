#!/usr/bin/env python
# debug_fotmob_endpoint.py
# شغل هاد الـ script على جهازك باش نكتشفوا الـ endpoint الصحيح
# python debug_fotmob_endpoint.py

import hashlib, hmac, time, base64, json, cloudscraper

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
    "Origin": "https://www.fotmob.com",
    "Accept": "application/json, text/plain, */*",
})

# Hakimi = well-known player, should exist
HAKIMI_ID = 961995

print("=== Testing Fotmob endpoints for Hakimi (id=961995) ===\n")

endpoints = [
    f"/api/playerData?id={HAKIMI_ID}",
    f"/api/player?id={HAKIMI_ID}",
    f"/api/players/{HAKIMI_ID}",
    f"/api/playerStats?playerId={HAKIMI_ID}",
    f"/api/playerStats?id={HAKIMI_ID}",
    f"/api/playercard?id={HAKIMI_ID}",
    f"/api/v1/playerData?id={HAKIMI_ID}",
    f"/api/v2/player?id={HAKIMI_ID}",
    f"/api/playerprofile?id={HAKIMI_ID}",
    f"/players/{HAKIMI_ID}/achraf-hakimi",
    f"/api/transfer/playerData?id={HAKIMI_ID}",
]

BASE = "https://www.fotmob.com"
for ep in endpoints:
    try:
        r = scraper.get(BASE + ep, timeout=10)
        status = r.status_code
        if status == 200:
            try:
                d = r.json()
                print(f"✅ [{status}] {ep}")
                print(f"   Keys: {list(d.keys())[:8]}")
                # Save sample response
                with open("fotmob_sample_response.json", "w") as f:
                    json.dump(d, f, indent=2, ensure_ascii=False)
                print(f"   → Saved to fotmob_sample_response.json")
            except:
                print(f"✅ [{status}] {ep} (HTML, {len(r.text)} chars)")
        else:
            print(f"❌ [{status}] {ep}")
    except Exception as e:
        print(f"💥 [ERR] {ep}: {e}")

print("\n=== Also checking what the actual page network calls look like ===")
print("→ Go to fotmob.com/players/961995/achraf-hakimi in browser")
print("→ Open DevTools → Network tab → filter 'playerData' or 'player'")
print("→ Copy the exact URL and paste it here")
