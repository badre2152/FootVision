# diagnose_clubelo_coverage.py
# Determines WHY Botola Pro / Saudi Pro League / UAE Pro League / Egyptian
# Premier League / Super League Greece got zero ClubElo matches: does
# ClubElo genuinely not track these leagues at all, or is it a naming
# mismatch that _match_club() could be fixed to handle?
#
# Usage:
#   python diagnose_clubelo_coverage.py

from ingestion.loaders.clubelo_loader import _fetch_clubelo_csv
from datetime import date

rows = _fetch_clubelo_csv(date.today().isoformat())
if not rows:
    print("No data fetched at all — check network/date fallback.")
    raise SystemExit(1)

print(f"Total clubs in ClubElo feed: {len(rows)}\n")

# Our non-European clubs that failed to match, from the live run's logs
MISSING_CLUBS = [
    "Raja Casablanca", "Wydad AC", "Al-Hilal SFC", "RS Berkane",
    "FAR Rabat", "Al Ahly", "Pyramids FC", "Al-Fateh SC",
    "Al Ain", "Olympiacos", "Al-Ittihad", "Stade Rennais FC",
]

all_names = [r.get("Club", "") for r in rows]

print("=" * 70)
print("STEP 1 — Does ANY club name in the feed contain a keyword from our")
print("missing clubs? (case-insensitive substring, both directions)")
print("=" * 70)
for missing in MISSING_CLUBS:
    keywords = [w for w in missing.lower().split() if len(w) > 3]
    matches = []
    for name in all_names:
        name_lower = name.lower()
        if any(kw in name_lower for kw in keywords) or missing.lower() in name_lower:
            matches.append(name)
    print(f"\n'{missing}' (keywords: {keywords}):")
    if matches:
        print(f"  POSSIBLE MATCHES FOUND: {matches[:5]}")
    else:
        print("  NO similar name anywhere in the feed — ClubElo likely doesn't track this club at all.")

print("\n" + "=" * 70)
print("STEP 2 — Sample of ALL club names containing 'Casablanca', 'Morocco',")
print("'Saudi', 'Egypt', 'Emirates', or 'Greece' region hints (broad net)")
print("=" * 70)
for hint in ["casablanca", "moroc", "saudi", "egypt", "emirates", "greece", "botola"]:
    found = [n for n in all_names if hint in n.lower()]
    print(f"  '{hint}': {found if found else '(none)'}")

print("\n\nCopy everything above and send it back.")