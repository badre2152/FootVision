# quick_test_arctic_shift.py
# Run this FIRST — no API key needed, fastest way to confirm the source works.
#
# Usage: python quick_test_arctic_shift.py

import sys
sys.path.insert(0, ".")

from ingestion.loaders.arctic_shift_collector import _collect_player_texts

TEST_PLAYERS = ["Achraf Hakimi", "Youssef En-Nesyri"]

for player in TEST_PLAYERS:
    print(f"\n{'='*60}\nTesting: {player}\n{'='*60}")
    texts = _collect_player_texts(player)
    print(f"Found {len(texts)} texts.")
    for i, t in enumerate(texts[:5]):
        print(f"  [{i}] {t[:100]}")
    if not texts:
        print("  (no results — could be low Reddit discussion volume, not necessarily a bug)")
