# quick_test_youtube.py
# Run this AFTER setting YOUTUBE_API_KEY in .env.
# Tests only 2 players (uses ~200-300 quota units, out of 10,000/day).
#
# Usage: python quick_test_youtube.py

import sys
sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()  # reads .env — if you don't have python-dotenv, set the env var manually instead

from ingestion.loaders.youtube_collector import _try_build_youtube_client, _collect_player_texts

TEST_PLAYERS = ["Achraf Hakimi", "Youssef En-Nesyri"]

youtube, HttpError = _try_build_youtube_client()
if youtube is None:
    print("FAILED to build YouTube client — check YOUTUBE_API_KEY in .env")
    sys.exit(1)

print("YouTube client built successfully.\n")
quota_tracker = {}

for player in TEST_PLAYERS:
    print(f"{'='*60}\nTesting: {player}\n{'='*60}")
    texts = _collect_player_texts(youtube, HttpError, player, quota_tracker)
    print(f"Found {len(texts)} texts. Quota used so far: {quota_tracker.get('units_used', 0)}")
    for i, t in enumerate(texts[:5]):
        print(f"  [{i}] {t[:100]}")
    if not texts:
        print("  (no results — check the video titles/comments actually mention the player)")

print(f"\nTotal quota used: {quota_tracker.get('units_used', 0)} / 10000 daily limit")
