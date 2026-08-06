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
    seed_tokens = seed_name.strip().split()
    cand_tokens = candidate_name.strip().split()
    if not seed_tokens or not cand_tokens:
        return False
    seed_surname = seed_tokens[-1]
    best = process.extractOne(seed_surname, cand_tokens, scorer=fuzz.ratio)
    return bool(best) and best[1] >= threshold


def search_understat(name: str) -> list[dict]:
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
    rows = []
    for _, player in seed_df.iterrows():
        name  = player["player_name"]
        club  = player.get("club", "")
        query = name

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


_SOFASCORE_SEARCH_URL = "https://api.sofascore.com/api/v1/search/players?q={name}"
_SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer":    "https://www.sofascore.com/",
    "Accept":     "application/json",
}


def search_sofascore(name: str) -> list[dict]:
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

        flat = []
        for c in candidates:
            p = c.get("player", c)
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


_FLASHSCORE_SEARCH_URL = "https://www.flashscore.com/"
_FLASHSCORE_PLAYER_URL_RE = re.compile(r"/player/([a-z0-9-]+)/([A-Za-z0-9]+)/?$")


def _flashscore_search_one(page, name: str) -> list[dict]:
    candidates: list[dict] = []
    try:
        page.goto(_FLASHSCORE_SEARCH_URL, timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(1000, 1800))

        search_trigger = page.locator("#search-window").first
        search_trigger.click(timeout=8000)

        search_box = page.locator("input.searchInput__input").first
        search_box.wait_for(state="visible", timeout=8000)
        search_box.fill(name, timeout=5000)

        page.wait_for_timeout(random.randint(1200, 2000))

        results = page.locator("a.searchResult").all()
        soccer_results = []
        for result in results[:20]:
            category_el = result.locator(".searchResult__participantCategory").first
            category = (category_el.inner_text(timeout=1500) or "") if category_el.count() else ""
            if not category.strip().lower().startswith("soccer"):
                continue

            name_el = result.locator(".searchResult__participantName").first
            label = (name_el.inner_text(timeout=1500) or "").strip() if name_el.count() else ""
            club = category.split(",", 1)[1].strip() if "," in category else ""
            href = result.get_attribute("href") or ""
            soccer_results.append({"name": label, "club": club, "href": href})

        context = page.context
        for cand in soccer_results[:6]:
            href = cand["href"]
            if not href:
                logger.info("FlashScore: empty href for candidate '%s', skipping", cand["name"])
                continue
            new_page = context.new_page()
            try:
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

            candidate_keys = [f"{c['name']} {c['club']}" for c in candidates]
            query_key      = f"{name} {club}"

            best_match, score, idx = process.extractOne(
                query_key, candidate_keys, scorer=fuzz.WRatio
            )

            matched   = candidates[idx]
            surname_ok = _surname_matches(name, matched["name"])
            confident = score >= 80 and surname_ok

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