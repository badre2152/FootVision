# diagnose_tm_positions_v3.py
# Checks whether the ".detail-position__position" selector is matching
# GENUINE distinct secondary positions, or duplicated/unrelated elements
# elsewhere on the page (suspected, since v2's real run showed
# tm_num_positions == 4 for nearly every player regardless of role —
# that uniformity is a strong signal of over-matching, not real diversity).
#
# Usage:
#   python diagnose_tm_positions_v3.py

import pandas as pd
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

seed = pd.read_csv("seeds/players_49.csv")
row = seed[seed["player_name"].str.contains("Hakimi", case=False, na=False)].iloc[0]
tm_id, tm_slug = int(row["tm_id"]), row["tm_slug"]
url = f"https://www.transfermarkt.com/{tm_slug}/profil/spieler/{tm_id}"
print("Testing:", row["player_name"], "->", url)

resp = requests.get(url, headers=HEADERS, timeout=15)
soup = BeautifulSoup(resp.text, "html.parser")

boxes = soup.select(".detail-position__position")
print(f"\nFound {len(boxes)} elements matching '.detail-position__position':\n")

for i, b in enumerate(boxes):
    classes = b.get("class")
    parent = b.parent
    grandparent = parent.parent if parent else None
    print(f"[{i}] tag=<{b.name}> class={classes}")
    print(f"     text={b.get_text(strip=True)!r}")
    print(f"     parent=<{parent.name if parent else None} class={parent.get('class') if parent else None}>")
    print(f"     grandparent=<{grandparent.name if grandparent else None} class={grandparent.get('class') if grandparent else None}>")
    print()

print("Copy everything above and send it back.")