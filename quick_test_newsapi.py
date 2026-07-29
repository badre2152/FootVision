# quick_test_newsapi.py
# Run this AFTER setting NEWSAPI_KEY in .env.
#
# Usage: python quick_test_newsapi.py

import sys
import os
sys.path.insert(0, ".")

from ingestion.loaders.newsapi_collector import _collect_player_articles

TEST_PLAYERS = ["Achraf Hakimi", "Youssef En-Nesyri"]

api_key = os.getenv("NEWSAPI_KEY")
if not api_key:
    print("FAILED — NEWSAPI_KEY not found in .env")
    sys.exit(1)

print("NEWSAPI_KEY found.\n")

for player in TEST_PLAYERS:
    print(f"{'='*60}\nTesting: {player}\n{'='*60}")
    texts = _collect_player_articles(api_key, player)
    print(f"Found {len(texts)} texts.")
    for i, t in enumerate(texts[:5]):
        print(f"  [{i}] {t[:100]}")
    if not texts:
        print("  (no results — check newsapi.org dashboard for rate-limit/error status)")
