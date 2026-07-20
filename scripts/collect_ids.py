#!/usr/bin/env python
# scripts/collect_ids.py
# Helper to resolve understat_id, sofascore_id, and flashscore_id for all 49 players.
#
# Strategy:
#   1. Search each site's API/search endpoint with player name
#   2. Fuzzy match results against (player_name + club) from seed
#   3. Export CSV with suggested IDs for manual review (~15-20% need manual fix)
#
# Estimated effort: ~80-85% automated, rest manual = 2-4 hours total.
# flashscore is slower (headless browser per player) — budget more time for 49 players.
#
# Run:
#   python scripts/collect_ids.py --source understat  --output seeds/understat_ids_draft.csv
#   python scripts/collect_ids.py --source sofascore  --output seeds/sofascore_ids_draft.csv
#   python scripts/collect_ids.py --source flashscore --output seeds/flashscore_ids_draft.csv

import argparse
import re
import time
import logging
import random

import pandas as pd
import requests
from rapidfuzz import process, fuzz

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger("collect_ids")

SEED_PATH = "seeds/players_49.csv"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _surname_matches(seed_name: str, candidate_name: str, threshold: int = 80) -> bool:
    """
    Guards against WRatio's token-set behavior, which scores names highly
    when ANY single token overlaps (e.g. shared first name "Mehdi"/"Omar")
    even if the surname is completely different. WRatio alone matched
    'Issa Diop' to 'Traore Issa' at score=85 — same first name, wrong
    person entirely.

    Treats the LAST token of the seed player's full name as the surname
    (works for "El Mehdi Al Harrar" -> "Harrar", "Issa Diop" -> "Diop") and
    requires it to appear, fuzzily, among the candidate's name tokens.
    Candidate names from these sources are often "Surname First" or
    "First Surname" in either order, so token-level comparison (not
    substring) is required.
    """
    seed_tokens = seed_name.strip().split()
    cand_tokens = candidate_name.strip().split()
    if not seed_tokens or not cand_tokens:
        return False
    seed_surname = seed_tokens[-1]
    best = process.extractOne(seed_surname, cand_tokens, scorer=fuzz.ratio)
    return bool(best) and best[1] >= threshold


# ── Understat ─────────────────────────────────────────────────────────────────

def search_understat(name: str) -> list[dict]:
    """
    Understat player search endpoint.
    Returns list of {id, player_name, team_title, ...}
    """
    try:
        resp = requests.get(
            "https://understat.com/main/getPlayersName/",
            params={"q": name},
            headers=_UA,
            timeout=10,
        )
        if resp.ok:
            return resp.json()
    except requests.RequestException as e:
        logger.warning("Understat search failed for '%s': %s", name, e)
    return []


def resolve_understat_ids(seed_df: pd.DataFrame) -> pd.DataFrame:
    """Fuzzy-matches seed players against Understat search results."""
    rows = []
    for _, player in seed_df.iterrows():
        name  = player["player_name"]
        club  = player.get("club", "")
        query = name  # Search by name only; fuzzy match on full key

        candidates = search_understat(query)
        time.sleep(1.5)

        if not candidates:
            rows.append({
                "player_id":     player["player_id"],
                "player_name":   name,
                "understat_id":  None,
                "matched_name":  None,
                "match_score":   0,
                "needs_review":  True,
            })
            continue

        # Build match key: "player_name club"
        candidate_keys = [f"{c.get('player_name', '')} {c.get('team_title', '')}" for c in candidates]
        query_key      = f"{name} {club}"

        best_match, score, idx = process.extractOne(
            query_key, candidate_keys, scorer=fuzz.WRatio
        )

        matched   = candidates[idx]
        uid       = matched.get("id")
        surname_ok = _surname_matches(name, matched.get("player_name", ""))
        confident = score >= 85 and surname_ok

        rows.append({
            "player_id":     player["player_id"],
            "player_name":   name,
            "understat_id":  uid if confident else None,
            "matched_name":  matched.get("player_name"),
            "matched_club":  matched.get("team_title"),
            "match_score":   score,
            "needs_review":  not confident,
        })
        logger.info(
            "%s → %s (score=%d)%s",
            name,
            matched.get("player_name"),
            score,
            " ⚠ REVIEW (surname mismatch)" if not surname_ok and score >= 85
            else " ⚠ REVIEW" if not confident else "",
        )

    return pd.DataFrame(rows)


# ── SofaScore ─────────────────────────────────────────────────────────────────

_SOFASCORE_SEARCH_URL = "https://api.sofascore.com/api/v1/search/players?q={name}"
_SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer":    "https://www.sofascore.com/",
    "Accept":     "application/json",
}


def search_sofascore(name: str) -> list[dict]:
    """SofaScore player search. Returns list of player dicts."""
    try:
        resp = requests.get(
            _SOFASCORE_SEARCH_URL.format(name=requests.utils.quote(name)),
            headers=_SOFASCORE_HEADERS,
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            return data.get("players", [])
    except requests.RequestException as e:
        logger.warning("SofaScore search failed for '%s': %s", name, e)
    return []


def resolve_sofascore_ids(seed_df: pd.DataFrame) -> pd.DataFrame:
    """Fuzzy-matches seed players against SofaScore search results."""
    rows = []
    for _, player in seed_df.iterrows():
        name  = player["player_name"]
        club  = player.get("club", "")

        candidates = search_sofascore(name)
        time.sleep(1.5)

        if not candidates:
            rows.append({
                "player_id":      player["player_id"],
                "player_name":    name,
                "sofascore_id":   None,
                "matched_name":   None,
                "match_score":    0,
                "needs_review":   True,
            })
            continue

        # SofaScore returns nested: {"player": {"id": ..., "name": ..., "team": ...}}
        flat = []
        for c in candidates:
            p = c.get("player", c)  # handle both formats
            flat.append({
                "id":   p.get("id"),
                "name": p.get("name", ""),
                "team": p.get("team", {}).get("name", "") if isinstance(p.get("team"), dict) else "",
            })

        candidate_keys = [f"{c['name']} {c['team']}" for c in flat]
        query_key      = f"{name} {club}"

        best_match, score, idx = process.extractOne(
            query_key, candidate_keys, scorer=fuzz.WRatio
        )

        matched   = flat[idx]
        surname_ok = _surname_matches(name, matched["name"])
        confident = score >= 85 and surname_ok

        rows.append({
            "player_id":      player["player_id"],
            "player_name":    name,
            "sofascore_id":   matched["id"] if confident else None,
            "matched_name":   matched["name"],
            "matched_club":   matched["team"],
            "match_score":    score,
            "needs_review":   not confident,
        })
        logger.info(
            "%s → %s (score=%d)%s",
            name,
            matched["name"],
            score,
            " ⚠ REVIEW (surname mismatch)" if not surname_ok and score >= 85
            else " ⚠ REVIEW" if not confident else "",
        )

    return pd.DataFrame(rows)


# ── FlashScore ────────────────────────────────────────────────────────────────
#
# FlashScore has no JSON search endpoint — search is a Radix UI modal dialog
# rendered client-side. This collector drives headless Chromium (Playwright)
# to open the modal, type into the search input, and read suggestion results
# out of the DOM.
#
# Confirmed via live DevTools inspection (2026-06-18): result links are NOT
# /player/{slug}/{id}/ as originally assumed — they're query-param links
# (/?r={type}:{id}, opaque alphanumeric id, no slug). See the result-parsing
# block below for the full confirmed markup.
#
# Slower than the requests-based collectors above (~5-8s/player vs ~1.5s) and
# more fragile — DOM selectors here are best-effort, same caveat as
# flashscore_loader.py. Requires: playwright install chromium

_FLASHSCORE_SEARCH_URL = "https://www.flashscore.com/"


# Confirmed via live DevTools + manual click-through (2026-06-18):
#   <a class="searchResult" href="/?r=4:MqFBkBsp">
#     <div class="searchResult__participantName">Hakimi Achraf<span class="...flag" title="Morocco"></span></div>
#     <div class="searchResult__participantCategory">Soccer, PSG</div>
#   </a>
# Key findings that changed the original (untested) assumptions:
#   - The search result href (/?r=4:ID) does NOT contain the slug. Clicking
#     a result triggers a CLIENT-SIDE (JS) navigation to the real URL
#     /player/{slug}/{id}/ — there is no server redirect to follow with a
#     plain HTTP request; Playwright must actually click and let the SPA
#     route, then read page.url() to learn the slug.
#   - The slug is NOT cosmetic. Manually testing a placeholder slug with the
#     correct id (/player/x/MqFBkBsp/) returned a 404 — FlashScore's player
#     route validates slug+id together, so a wrong/fake slug breaks the
#     player page even when the id is exactly right.
#   - A common name returns many unrelated people across sports/countries
#     (e.g. searching "Hakimi" returned 17 results: footballers, an eSports
#     profile, Malaysian players, a tennis player). MUST filter to
#     "Soccer, " category prefix before matching, or noise from other
#     sports wins.
_FLASHSCORE_PLAYER_URL_RE = re.compile(r"/player/([a-z0-9-]+)/([A-Za-z0-9]+)/?$")


def _flashscore_search_one(page, name: str) -> list[dict]:
    """
    Opens FlashScore's search modal, types `name`, and reads soccer-player
    candidates. For each candidate this clicks through to the real player
    page (in a fresh tab) to read the actual slug out of page.url() — the
    search dropdown's href does not contain it, and a wrong slug 404s even
    with the correct id (confirmed manually), so it cannot be guessed.

    Search itself is a 2-step interaction:
      1. Click the search trigger (#search-window) to open the modal.
      2. The modal renders a Radix UI dialog with a NEW <input> element
         (class="searchInput__input") that doesn't exist in the DOM before
         the click — a direct `.fill()` on a pre-existing locator times out.
      The dialog uses Radix-generated dynamic IDs (radix-«r0») which are NOT
      stable across loads — never select on those, only on
      `.searchInput__input` / `.searchResult`, which are real CSS classes.
    """
    candidates: list[dict] = []
    try:
        page.goto(_FLASHSCORE_SEARCH_URL, timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(1000, 1800))

        search_trigger = page.locator("#search-window").first
        search_trigger.click(timeout=8000)

        search_box = page.locator("input.searchInput__input").first
        search_box.wait_for(state="visible", timeout=8000)
        search_box.fill(name, timeout=5000)

        # Modal note: "Please type at least 2 characters" — results render
        # after a short debounce.
        page.wait_for_timeout(random.randint(1200, 2000))

        results = page.locator("a.searchResult").all()
        soccer_results = []
        for result in results[:20]:  # common names return many cross-sport hits
            category_el = result.locator(".searchResult__participantCategory").first
            category = (category_el.inner_text(timeout=1500) or "") if category_el.count() else ""
            if not category.strip().lower().startswith("soccer"):
                continue  # skip tennis/eSports/other-sport namesakes

            name_el = result.locator(".searchResult__participantName").first
            label = (name_el.inner_text(timeout=1500) or "").strip() if name_el.count() else ""
            club = category.split(",", 1)[1].strip() if "," in category else ""
            # Read href NOW, while the search modal's DOM is still fresh.
            # Re-reading element.get_attribute("href") later, mid click-through
            # loop, risks a stale/changed locator if the underlying page's DOM
            # shifts during the 1.5-2.5s navigation of earlier candidates
            # (live score updates, ads, modal state) — confirmed in practice:
            # the modal's FIRST result (correct match) silently produced a
            # wrong/empty href on click-through while later results worked,
            # consistent with locator staleness rather than a search-ranking
            # issue. Capturing all hrefs upfront removes that race entirely.
            href = result.get_attribute("href") or ""
            soccer_results.append({"name": label, "club": club, "href": href})

        # Click through each soccer candidate (in a new tab, to keep the
        # search modal/results intact for the remaining candidates) and
        # read the real slug+id off the resulting URL.
        context = page.context
        for cand in soccer_results[:6]:  # cap clicks per name to bound runtime
            href = cand["href"]
            if not href:
                logger.info("FlashScore: empty href for candidate '%s', skipping", cand["name"])
                continue
            new_page = context.new_page()
            try:
                # The href itself (/?r=4:ID) IS enough to trigger FlashScore's
                # client-side router to the real /player/{slug}/{id}/ URL when
                # loaded directly — confirmed: this is the same param format
                # the SPA reads on click, so navigating to it lets the app's
                # own redirect logic resolve the slug for us, without
                # depending on modal/dropdown DOM state in a second tab.
                new_page.goto(
                    f"https://www.flashscore.com{href}",
                    timeout=15000,
                    wait_until="domcontentloaded",
                )
                new_page.wait_for_timeout(random.randint(1500, 2500))
                final_url = new_page.url
                match = _FLASHSCORE_PLAYER_URL_RE.search(final_url)
                if match:
                    candidates.append({
                        "id": match.group(2),
                        "slug": match.group(1),
                        "name": cand["name"],
                        "club": cand["club"],
                    })
                else:
                    logger.info(
                        "FlashScore: no /player/ URL after navigating '%s' (got %s)",
                        cand["name"], final_url,
                    )
            except Exception as e:
                logger.warning("FlashScore click-through failed for '%s': %s", cand["name"], e)
            finally:
                new_page.close()
    except Exception as e:
        logger.warning("FlashScore search failed for '%s': %s", name, e)
    return candidates


def resolve_flashscore_ids(seed_df: pd.DataFrame) -> pd.DataFrame:
    """Fuzzy-matches seed players against FlashScore search suggestions."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "Playwright not installed. Run: pip install playwright "
            "&& playwright install chromium"
        )
        return pd.DataFrame()

    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 850},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = context.new_page()

        for _, player in seed_df.iterrows():
            name = player["player_name"]
            club = player.get("club", "")

            candidates = _flashscore_search_one(page, name)
            time.sleep(random.uniform(1.5, 2.5))

            if not candidates:
                rows.append({
                    "player_id":       player["player_id"],
                    "player_name":     name,
                    "flashscore_id":   None,
                    "flashscore_slug": None,
                    "matched_name":    None,
                    "matched_club":    None,
                    "match_score":     0,
                    "needs_review":    True,
                })
                continue

            # Match on "name club" against each candidate's "name club" —
            # club comes from the real searchResult__participantCategory
            # text (e.g. "Soccer, PSG" -> club="PSG"), confirmed live. This
            # is what disambiguates Achraf Hakimi (PSG) from the many other
            # "Hakimi" namesakes FlashScore returns (Malaysia, Tunisia, etc.)
            candidate_keys = [f"{c['name']} {c['club']}" for c in candidates]
            query_key      = f"{name} {club}"

            best_match, score, idx = process.extractOne(
                query_key, candidate_keys, scorer=fuzz.WRatio
            )

            matched   = candidates[idx]
            surname_ok = _surname_matches(name, matched["name"])
            confident = score >= 80 and surname_ok  # lower bar than API sources — labels are noisier here

            rows.append({
                "player_id":       player["player_id"],
                "player_name":     name,
                "flashscore_id":   matched["id"] if confident else None,
                "flashscore_slug": matched["slug"] if confident else None,
                "matched_name":    matched["name"],
                "matched_club":    matched["club"],
                "match_score":     score,
                "needs_review":    not confident,
            })
            logger.info(
                "%s → %s (%s, score=%d)%s",
                name, matched["name"], matched["club"], score,
                " ⚠ REVIEW (surname mismatch)" if not surname_ok and score >= 80
                else " ⚠ REVIEW" if not confident else "",
            )

        context.close()
        browser.close()

    return pd.DataFrame(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Resolve player IDs for Bronze sources")
    p.add_argument(
        "--source",
        choices=["understat", "sofascore", "flashscore"],
        required=True,
        help="Which source to resolve IDs for",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: seeds/{source}_ids_draft.csv)",
    )
    args = p.parse_args()

    seed_df = pd.read_csv(SEED_PATH)
    logger.info("Loaded %d players from %s", len(seed_df), SEED_PATH)

    if args.source == "understat":
        result_df  = resolve_understat_ids(seed_df)
        id_col     = "understat_id"
    elif args.source == "sofascore":
        result_df  = resolve_sofascore_ids(seed_df)
        id_col     = "sofascore_id"
    else:
        result_df  = resolve_flashscore_ids(seed_df)
        id_col     = "flashscore_id"

    output = args.output or f"seeds/{args.source}_ids_draft.csv"

    # Same-candidate collision guard: two different seed players resolving to
    # the SAME matched_name+matched_club means the upstream search/match step
    # picked one external profile for both of them — one is right, the other
    # is wrong, and we can't tell which from the score alone. This must be
    # checked on matched_name+matched_club (not on the final id column),
    # because the surname filter above already nulls out the id for the
    # rejected row — checking only the id column would miss the collision
    # entirely once one side is empty (confirmed: this is exactly what
    # happened with 'Bilal El Khannouss' / 'Bilal Nadir' both resolving to
    # 'Nadir Bilal, MARSEILLE' — only one side kept a non-null id, so a
    # check on the id column alone saw no duplicate).
    match_key = result_df["matched_name"].fillna("") + "|" + result_df.get("matched_club", "").fillna("")
    collision_mask = (match_key != "|") & match_key.duplicated(keep=False)
    if collision_mask.any():
        for key in match_key[collision_mask].unique():
            claimants = result_df.loc[match_key == key, "player_name"].tolist()
            logger.warning(
                "Collision: candidate '%s' matched by multiple seed players: %s — flagging all for review",
                key.replace("|", " / "), ", ".join(claimants),
            )
        result_df.loc[collision_mask, id_col] = None
        result_df.loc[collision_mask, "needs_review"] = True

    # Duplicate-ID guard: belt-and-suspenders check on the final id itself,
    # in case two DIFFERENT matched_name/club pairs somehow still produced
    # the same external id.
    dup_mask = result_df[id_col].notna() & result_df.duplicated(subset=[id_col], keep=False)
    if dup_mask.any():
        dup_ids = result_df.loc[dup_mask, id_col].unique().tolist()
        for dup_id in dup_ids:
            names = result_df.loc[result_df[id_col] == dup_id, "player_name"].tolist()
            logger.warning(
                "Duplicate %s '%s' claimed by multiple players: %s — flagging all for review",
                id_col, dup_id, ", ".join(names),
            )
        result_df.loc[dup_mask, id_col] = None
        result_df.loc[dup_mask, "needs_review"] = True

    result_df.to_csv(output, index=False)

    resolved   = result_df[id_col].notna().sum()
    needs_review = result_df["needs_review"].sum()

    logger.info("=" * 50)
    logger.info("Results: %d/%d resolved automatically", resolved, len(result_df))
    logger.info("Needs manual review: %d players", needs_review)
    logger.info("Output: %s", output)
    logger.info("Next: open the CSV, fill in missing/wrong IDs, then copy to players_49.csv")


if __name__ == "__main__":
    main()