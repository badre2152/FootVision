# dashboard/pitch_renderer_html.py
# Football Atlas — Tactical Board, premium HTML/CSS pitch renderer
#
# Added 2026-08-02 (Tactical Board redesign), extended 2026-08-02 (v2:
# zoom/pan/fullscreen toolbar, tactical spine lines, floodlight+grain
# polish, position legend — see CHANGELOG.md entry #7). Renders the same
# formation data dashboard/pitch_renderer.py has always produced — same
# assign_xi_to_formation(), same FORMATIONS coordinates, same
# find_player_photo() lookup — as a self-contained HTML document meant for
# st.iframe()/st.components.v1.html(), instead of a static matplotlib
# image.
#
# WHY A SEPARATE FILE INSTEAD OF EDITING pitch_renderer.py
# ─────────────────────────────────────────────────────────────────────────
# render_pitch() (matplotlib) is protected by 19 tests in
# tests/test_pitch_renderer.py and this was an explicit constraint on the
# redesign ("don't change any backend logic or functionality"). A static
# image genuinely cannot do CSS gradients, hover states, zoom, or entrance
# animations — no styling of a matplotlib Figure gets there — so the
# rendering TECHNOLOGY has to change to deliver the brief. Keeping that
# change in a new file means dashboard/pitch_renderer.py — and every test
# that protects it — is untouched: zero risk to existing coverage, and
# render_pitch() stays available if anything else in this codebase still
# wants a plain static image (CSV/PDF export, etc.).
#
# WHAT THIS MODULE DOES NOT DO
# ─────────────────────────────────────────────────────────────────────────
# No formation math, no slot assignment, no scoring. It takes the already-
# assigned list of dicts (x, y, player_name, position, player_id, plus
# whatever optional display-only fields the caller merged in — DSSScore,
# club — assign_xi_to_formation() itself is never touched) and turns it
# into markup + a tiny bit of client-side JS for zoom/pan/fullscreen. If
# the shape of the XI is wrong, the bug is in dashboard/pitch_renderer.py
# or the calling page, not here.
#
# COORDINATE SYSTEM
# ─────────────────────────────────────────────────────────────────────────
# mplsoccer's `Pitch(pitch_type="opta")` uses a NORMALIZED 0-100 x 0-100
# coordinate space for both axes (confirmed via Pitch().dim — this
# project's own FORMATIONS comment calling it "0-80 width" was incorrect;
# the real pitch aspect ratio, 105m x 68m, is applied by mplsoccer at DRAW
# time, not baked into the coordinate values). To reproduce the exact same
# visual layout here: left% = x, top% = y, and the pitch CONTAINER's
# aspect-ratio (not the coordinate space) carries the real 105:68 shape.
#
# ZOOM/PAN/FULLSCREEN — WHY IT'S BUILT THIS WAY
# ─────────────────────────────────────────────────────────────────────────
# st.iframe(html, height="content") measures content height ONCE at load.
# If zooming resized the pitch's actual layout box, the iframe's fixed
# height would either clip the zoomed-in view or leave dead space when
# zoomed out. Instead, zoom is a CSS `transform: scale()` applied INSIDE a
# fixed-aspect-ratio viewport with `overflow: hidden` — the outer box
# (and therefore the iframe height Streamlit measured) never changes size;
# only what's visually drawn inside it does. Panning (drag, only active
# once zoomed in) is a second transform: translate() layered on the same
# element. Fullscreen uses the standard Fullscreen API on the whole
# board-wrap; if the browser/embedding context doesn't support it, the
# button hides itself via a feature check instead of doing nothing on
# click.

from __future__ import annotations

import base64
import html
from pathlib import Path

from dashboard.pitch_renderer import find_player_photo

# Real pitch dimensions (meters) — used only to compute marking sizes as
# percentages of the 0-100 x 0-100 coordinate space so they render as
# correctly-proportioned circles/rectangles once the container's
# aspect-ratio: 105/68 stretches that space into the real pitch shape.
_PITCH_LENGTH_M = 105.0
_PITCH_WIDTH_M = 68.0
_CENTER_CIRCLE_R_M = 9.15
_PENALTY_AREA_LENGTH_M = 16.5
_PENALTY_AREA_WIDTH_M = 40.32
_SIX_YARD_LENGTH_M = 5.5
_SIX_YARD_WIDTH_M = 18.32
_PENALTY_SPOT_M = 11.0

_CC_RX = _CENTER_CIRCLE_R_M / _PITCH_LENGTH_M * 100
_CC_RY = _CENTER_CIRCLE_R_M / _PITCH_WIDTH_M * 100
_PA_LEN = _PENALTY_AREA_LENGTH_M / _PITCH_LENGTH_M * 100
_PA_WID = _PENALTY_AREA_WIDTH_M / _PITCH_WIDTH_M * 100
_SY_LEN = _SIX_YARD_LENGTH_M / _PITCH_LENGTH_M * 100
_SY_WID = _SIX_YARD_WIDTH_M / _PITCH_WIDTH_M * 100
_PEN_SPOT_X = _PENALTY_SPOT_M / _PITCH_LENGTH_M * 100

# Position palette — richer/gradient-friendly than pitch_renderer.py's flat
# POSITION_COLORS (that constant stays as-is; matplotlib markers don't
# benefit from a two-stop gradient the way a CSS ring does).
POSITION_GRADIENTS: dict[str, tuple[str, str]] = {
    "GK": ("#f0d060", "#b8860b"),
    "DEF": ("#60a5fa", "#1d4ed8"),
    "MID": ("#34d399", "#047857"),
    "FWD": ("#f87171", "#b91c1c"),
}
_FALLBACK_GRADIENT = ("#9ca3af", "#4b5563")
_POSITION_ORDER = ["GK", "DEF", "MID", "FWD"]
_POSITION_LABELS = {"GK": "Goalkeepers", "DEF": "Defenders", "MID": "Midfielders", "FWD": "Forwards"}
_POSITION_CHIP = {"GK": "GK", "DEF": "D", "MID": "M", "FWD": "F"}


def _photo_data_uri(player_id: str | None) -> str | None:
    """Base64-embeds a player photo so it survives inside the sandboxed
    iframe (which can't resolve local filesystem paths). Reuses
    find_player_photo() — the same lookup pitch_renderer.py's matplotlib
    path already uses — so both renderers treat "no photo" the exact same
    way. Never raises; returns None on any read failure."""
    path = find_player_photo(player_id)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    ext = path.suffix.lower().lstrip(".")
    mime = "image/png" if ext == "png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _initials_marker(name: str) -> str:
    last = (name.split()[-1] if name else "?")
    return (last[0] if last else "?").upper()


def _club_monogram(club: str | None) -> str:
    """2-3 letter club monogram from the club name, e.g. 'Paris
    Saint-Germain' -> 'PSG', 'West Ham' -> 'WH'. Deliberately NOT a real
    club crest image — those are trademarked logos this project has no
    rights to reproduce. A generated text monogram gives the same "club
    badge" visual read (see the Champions League/PSG-Inter reference
    graphics) without that problem."""
    if not club:
        return ""
    words = [w for w in club.replace("-", " ").split() if w]
    letters = "".join(w[0] for w in words[:3]).upper()
    return letters or club[:2].upper()


def _pitch_markings_svg() -> str:
    """Static SVG markings layer — viewBox 0 0 100 100 matches the
    coordinate space player markers use (left%=x, top%=y), so no separate
    scaling math is needed between the two layers."""
    cx, cy = 50, 50
    return f'''
    <svg class="pitch-markings" viewBox="0 0 100 100" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="0.6" y="0.6" width="98.8" height="98.8" fill="none" stroke="white" stroke-opacity="0.55" stroke-width="0.35"/>
      <line x1="50" y1="0.6" x2="50" y2="99.4" stroke="white" stroke-opacity="0.55" stroke-width="0.35"/>
      <circle cx="{cx}" cy="{cy}" r="1" fill="white" fill-opacity="0.55"/>
      <ellipse cx="{cx}" cy="{cy}" rx="{_CC_RX:.2f}" ry="{_CC_RY:.2f}" fill="none" stroke="white" stroke-opacity="0.55" stroke-width="0.35"/>
      <rect x="0.6" y="{50 - _PA_WID/2:.2f}" width="{_PA_LEN:.2f}" height="{_PA_WID:.2f}" fill="none" stroke="white" stroke-opacity="0.55" stroke-width="0.35"/>
      <rect x="0.6" y="{50 - _SY_WID/2:.2f}" width="{_SY_LEN:.2f}" height="{_SY_WID:.2f}" fill="none" stroke="white" stroke-opacity="0.55" stroke-width="0.35"/>
      <circle cx="{_PEN_SPOT_X:.2f}" cy="50" r="0.8" fill="white" fill-opacity="0.55"/>
      <rect x="{100 - _PA_LEN - 0.6:.2f}" y="{50 - _PA_WID/2:.2f}" width="{_PA_LEN:.2f}" height="{_PA_WID:.2f}" fill="none" stroke="white" stroke-opacity="0.55" stroke-width="0.35"/>
      <rect x="{100 - _SY_LEN - 0.6:.2f}" y="{50 - _SY_WID/2:.2f}" width="{_SY_LEN:.2f}" height="{_SY_WID:.2f}" fill="none" stroke="white" stroke-opacity="0.55" stroke-width="0.35"/>
      <circle cx="{100 - _PEN_SPOT_X:.2f}" cy="50" r="0.8" fill="white" fill-opacity="0.55"/>
    </svg>
    '''


def _tactical_spine_svg(assigned_players: list[dict]) -> str:
    """Thin glowing line connecting each formation line's centroid
    (GK -> DEF -> MID -> FWD, whichever groups are actually present) —
    a common broadcast-graphic touch that reads the team's shape at a
    glance without cluttering individual player connections. Purely
    decorative/display — computed from the SAME x/y assign_xi_to_formation()
    already produced, nothing recalculated."""
    centroids = []
    for pos in _POSITION_ORDER:
        pts = [(p["x"], p["y"]) for p in assigned_players if p.get("position") == pos]
        if pts:
            cx = sum(x for x, _ in pts) / len(pts)
            cy = sum(y for _, y in pts) / len(pts)
            centroids.append((cx, cy))
    if len(centroids) < 2:
        return ""
    path_d = f"M {centroids[0][0]:.2f} {centroids[0][1]:.2f} " + " ".join(
        f"L {x:.2f} {y:.2f}" for x, y in centroids[1:]
    )
    return f'''
    <svg class="tactical-spine" viewBox="0 0 100 100" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <filter id="spine-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="0.6" result="blur"/>
          <feMerge>
            <feMergeNode in="blur"/>
            <feMergeNode in="SourceGraphic"/>
          </feMerge>
        </filter>
      </defs>
      <path d="{path_d}" fill="none" stroke="#d4af37" stroke-opacity="0.38"
            stroke-width="0.45" stroke-dasharray="1.2 1.4" stroke-linecap="round"
            filter="url(#spine-glow)"/>
    </svg>
    '''


def _grain_svg_data_uri() -> str:
    """A near-invisible SVG film-grain texture (feTurbulence) tiled over
    the pitch for a broadcast-quality "lit surface" feel instead of a
    flat CSS gradient. Static/deterministic (baseFrequency fixed) so it's
    identical on every render, not distracting motion."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120">'
        '<filter id="n"><feTurbulence type="fractalNoise" baseFrequency="0.9" '
        'numOctaves="2" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/>'
        '</filter><rect width="100%" height="100%" filter="url(#n)"/></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def render_pitch_html(
    assigned_players: list[dict],
    title: str = "Starting XI",
    subtitle: str | None = None,
    show_photos: bool = True,
) -> tuple[str, int]:
    """
    Builds a self-contained HTML document for the tactical board pitch,
    including a zoom/pan/fullscreen toolbar and position legend.

    assigned_players: same shape assign_xi_to_formation() already returns
        (player_id, player_name, position, x, y) — the caller MAY merge in
        extra display-only keys (dss_score: float, club: str) before
        calling this; both are optional and purely cosmetic (hover card).
    show_photos: identical meaning/behavior to render_pitch()'s param —
        False always shows the initials marker, even if a photo exists.

    Returns (html_string, recommended_iframe_height_px) — the height is
    only used as a fallback for callers stuck on the older
    st.components.v1.html() API; st.iframe(height="content") ignores it
    and measures the real rendered height instead.
    """
    cards = []
    for i, p in enumerate(assigned_players):
        pos = p.get("position", "")
        grad_start, grad_end = POSITION_GRADIENTS.get(pos, _FALLBACK_GRADIENT)
        name = p.get("player_name") or ""
        safe_name = html.escape(name)
        last_name = html.escape(name.split()[-1] if name else "")
        chip = html.escape(_POSITION_CHIP.get(pos, pos[:1]))
        club_mono = html.escape(_club_monogram(p.get("club")))
        photo_uri = _photo_data_uri(p.get("player_id")) if show_photos else None

        if photo_uri:
            avatar_html = f'<img class="pn-photo" src="{photo_uri}" alt="{safe_name}" draggable="false">'
        else:
            avatar_html = f'<div class="pn-fallback">{html.escape(_initials_marker(name))}</div>'

        meta_bits = []
        if p.get("club"):
            meta_bits.append(html.escape(str(p["club"])))
        dss = p.get("dss_score")
        if isinstance(dss, (int, float)):
            meta_bits.append(f"DSS {dss:.2f}")
        meta_html = " · ".join(meta_bits)

        cards.append(f'''
        <div class="player-node" style="left:{p.get("x", 50):.2f}%; top:{p.get("y", 50):.2f}%; --grad-a:{grad_start}; --grad-b:{grad_end}; --delay:{i * 55}ms;">
          <div class="pn-ring">
            {avatar_html}
            <span class="pn-chip">{chip}</span>
            {f'<span class="pn-club">{club_mono}</span>' if club_mono else ''}
          </div>
          <div class="pn-name">{last_name or "&nbsp;"}</div>
          {f'<div class="pn-tooltip"><strong>{safe_name}</strong><span>{pos}{" · " + meta_html if meta_html else ""}</span></div>' if (meta_html or safe_name) else ''}
        </div>
        ''')

    present_positions = [pos for pos in _POSITION_ORDER if any(p.get("position") == pos for p in assigned_players)]
    legend_html = "".join(
        f'''<div class="legend-item">
              <span class="legend-dot" style="background:linear-gradient(145deg,{POSITION_GRADIENTS[pos][0]},{POSITION_GRADIENTS[pos][1]})"></span>
              {html.escape(_POSITION_LABELS[pos])}
            </div>'''
        for pos in present_positions
    )

    safe_title = html.escape(title)
    safe_subtitle = html.escape(subtitle) if subtitle else ""
    grain_uri = _grain_svg_data_uri()

    doc = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg-void: #090c11;
    --border: rgba(255,255,255,0.08);
    --text-primary: #eef1f5;
    --text-secondary: #8891a0;
    --pitch-dark: #0c3a22;
    --pitch-mid: #145c34;
    --accent-gold: #d4af37;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0; background: transparent;
    font-family: 'Inter', -apple-system, sans-serif;
  }}
  .board-wrap {{
    background: radial-gradient(120% 140% at 20% -10%, #16202b 0%, var(--bg-void) 55%);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 22px 22px 18px 22px;
  }}
  .board-wrap.is-fullscreen {{
    display: flex; flex-direction: column; justify-content: center;
    width: 100vw; height: 100vh; border-radius: 0;
    padding: 28px;
  }}
  /* Fallback "maximize" — used when the real Fullscreen API is blocked
     (Streamlit's st.iframe sandbox doesn't include the allow-fullscreen
     token, so document.fullscreenEnabled is false in practice — see
     pitch_renderer_html.py's module docstring). Height is set via inline
     style by JS (computed from the real browser viewport, read through
     window.top since the sandboxed iframe has allow-same-origin) rather
     than a fixed CSS value, then Streamlit's own iframe auto-resize
     mechanism grows the REAL <iframe> on the page to match — this
     actually enlarges the embedded board on the page, not just a
     same-size overlay. */
  .board-wrap.is-maxed {{
    display: flex; flex-direction: column;
  }}
  .board-header {{
    display: flex; align-items: flex-start; justify-content: space-between;
    margin-bottom: 16px; padding: 0 4px; gap: 12px;
  }}
  .board-title {{
    font-family: 'Barlow Condensed', sans-serif;
    font-weight: 700; font-size: 26px; letter-spacing: 0.02em;
    color: var(--text-primary); text-transform: uppercase; margin: 0;
    position: relative; padding-bottom: 8px;
  }}
  .board-title::after {{
    content: "";
    position: absolute; left: 0; bottom: 0;
    width: 46px; height: 3px; border-radius: 2px;
    background: linear-gradient(90deg, var(--accent-gold), transparent);
  }}
  .board-subtitle-pill {{
    display: inline-flex; align-items: center; gap: 5px;
    margin-top: 8px;
    font-size: 11.5px; color: var(--text-secondary); font-weight: 600;
    letter-spacing: 0.03em;
    background: rgba(255,255,255,0.06);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 3px 11px 3px 9px;
  }}
  .board-subtitle-pill .dot {{
    width: 5px; height: 5px; border-radius: 50%;
    background: var(--accent-gold); flex-shrink: 0;
  }}
  .board-brand {{
    color: var(--accent-gold); font-family: 'Barlow Condensed', sans-serif;
    font-weight: 700; font-size: 13px; letter-spacing: 0.06em;
    white-space: nowrap; padding-top: 2px;
  }}

  /* ── toolbar ─────────────────────────────────────────────────── */
  .board-toolbar {{
    display: flex; align-items: center; gap: 6px;
    margin-bottom: 10px; padding: 0 2px;
  }}
  .tb-btn {{
    display: flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; border-radius: 8px;
    background: rgba(255,255,255,0.05);
    border: 1px solid var(--border);
    color: var(--text-primary);
    cursor: pointer; font-size: 15px; line-height: 1; user-select: none;
    transition: background 120ms ease, transform 120ms ease;
  }}
  .tb-btn:hover {{ background: rgba(255,255,255,0.12); transform: translateY(-1px); }}
  .tb-btn:active {{ transform: translateY(0); }}
  .tb-zoom-label {{
    min-width: 42px; text-align: center;
    font-size: 12px; color: var(--text-secondary); font-weight: 600;
  }}
  .tb-spacer {{ flex: 1; }}
  .tb-hint {{
    font-size: 11px; color: var(--text-secondary); opacity: 0.7;
    display: none;
  }}
  .board-wrap:not(.is-fullscreen) .tb-hint.show-hint {{ display: block; }}

  /* ── pitch viewport / zoom+pan layer ────────────────────────────── */
  .pitch-viewport {{
    position: relative;
    width: 100%;
    aspect-ratio: 105 / 68;
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 0 20px 50px -12px rgba(0,0,0,0.6), inset 0 0 0 1px rgba(255,255,255,0.06);
    transition: transform 320ms ease, box-shadow 320ms ease;
    transform-style: preserve-3d;
  }}
  /* Broadcast camera angle — OPTIONAL, off by default, toggled by the
     toolbar button. Worth flagging: none of the 4 reference images were
     actually tilted (all flat top-down) — this isn't "from the pics", it's
     an extra idea borrowed from other broadcast lineup graphics. Kept
     off by default and toggle-able rather than forced on for that reason.
     Applied to the WHOLE viewport (markings + players together as one
     flat plane), not the markings layer alone, so nothing shifts out of
     alignment with the player markers sitting in the same percentage
     coordinate space. */
  .pitch-viewport.tilted {{
    transform: perspective(1500px) rotateX(13deg) scale(0.97);
    box-shadow: 0 34px 60px -18px rgba(0,0,0,0.7), inset 0 0 0 1px rgba(255,255,255,0.06);
  }}
  .board-wrap.is-fullscreen .pitch-viewport {{
    width: min(90vw, 90vh * 1.5441);
    margin: 0 auto;
  }}
  .pitch-shell {{
    position: absolute; inset: 0;
    transform: translate(var(--pan-x, 0px), var(--pan-y, 0px)) scale(var(--zoom, 1));
    transform-origin: center center;
    transition: transform 90ms ease-out;
    cursor: grab;
    background:
      repeating-linear-gradient(
        90deg,
        var(--pitch-mid) 0%, var(--pitch-mid) 5.26%,
        var(--pitch-dark) 5.26%, var(--pitch-dark) 10.52%
      );
  }}
  .pitch-shell.is-panning {{ cursor: grabbing; transition: none; }}
  .pitch-shell::before {{
    content: "";
    position: absolute; inset: 0;
    background:
      radial-gradient(38% 30% at 8% 4%, rgba(255,255,255,0.16), transparent 60%),
      radial-gradient(38% 30% at 92% 4%, rgba(255,255,255,0.16), transparent 60%),
      radial-gradient(140% 100% at 50% -20%, rgba(255,255,255,0.08), transparent 55%),
      radial-gradient(90% 70% at 50% 120%, rgba(0,0,0,0.35), transparent 60%);
    pointer-events: none;
  }}
  .pitch-shell::after {{
    content: "";
    position: absolute; inset: 0;
    background-image: url('{grain_uri}');
    background-size: 140px 140px;
    opacity: 0.05;
    mix-blend-mode: overlay;
    pointer-events: none;
  }}
  .pitch-markings, .tactical-spine {{
    position: absolute; inset: 0; width: 100%; height: 100%;
  }}
  .player-node {{
    position: absolute;
    transform: translate(-50%, -50%);
    display: flex; flex-direction: column; align-items: center;
    z-index: 3;
    opacity: 0;
    animation: node-in 480ms cubic-bezier(.2,.8,.2,1) forwards;
    animation-delay: var(--delay);
    cursor: default;
  }}
  @keyframes node-in {{
    from {{ opacity: 0; transform: translate(-50%, -42%) scale(0.7); }}
    to   {{ opacity: 1; transform: translate(-50%, -50%) scale(1); }}
  }}
  .pn-ring {{
    position: relative;
    width: 13%; aspect-ratio: 1;
    min-width: 34px; min-height: 34px;
    border-radius: 50%;
    padding: 2.5px;
    background: linear-gradient(145deg, var(--grad-a), var(--grad-b));
    box-shadow:
      0 6px 14px -4px rgba(0,0,0,0.55),
      0 0 0 1px rgba(255,255,255,0.15),
      0 0 0 3px rgba(9,12,17,0.85),
      0 0 0 4px rgba(255,255,255,0.08);
    transition: transform 160ms ease, box-shadow 160ms ease;
  }}
  .player-node:hover .pn-ring {{
    transform: scale(1.12);
    box-shadow: 0 10px 22px -4px rgba(0,0,0,0.65), 0 0 0 2px rgba(255,255,255,0.35);
  }}
  .pn-photo, .pn-fallback {{
    width: 100%; height: 100%; border-radius: 50%;
    display: block; object-fit: cover;
    background: #1a222c;
    border: 2px solid var(--bg-void);
  }}
  .pn-fallback {{
    display: flex; align-items: center; justify-content: center;
    font-family: 'Barlow Condensed', sans-serif; font-weight: 700;
    font-size: 15px; color: #fff;
    background: linear-gradient(145deg, var(--grad-a), var(--grad-b));
  }}
  .pn-chip {{
    position: absolute; bottom: -3px; right: -3px;
    min-width: 15px; height: 15px; padding: 0 3px;
    border-radius: 999px;
    background: var(--bg-void);
    border: 1.5px solid var(--grad-a);
    color: #fff; font-size: 8.5px; font-weight: 700;
    font-family: 'Barlow Condensed', sans-serif;
    display: flex; align-items: center; justify-content: center;
    letter-spacing: 0.01em;
  }}
  .pn-club {{
    position: absolute; top: -4px; left: -4px;
    min-width: 17px; height: 17px; padding: 0 3px;
    border-radius: 5px;
    background: rgba(17,22,29,0.92);
    border: 1px solid rgba(255,255,255,0.22);
    color: #eef1f5; font-size: 7.5px; font-weight: 700;
    font-family: 'Inter', sans-serif; letter-spacing: 0.02em;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 2px 5px rgba(0,0,0,0.4);
  }}
  .pn-name {{
    margin-top: 5px;
    font-family: 'Barlow Condensed', sans-serif;
    font-weight: 700; font-size: 12.5px; letter-spacing: 0.02em;
    color: #fff; text-transform: uppercase;
    background: rgba(9,12,17,0.72);
    padding: 1.5px 7px;
    border-radius: 6px;
    white-space: nowrap;
    text-shadow: 0 1px 2px rgba(0,0,0,0.5);
  }}
  .pn-tooltip {{
    position: absolute;
    bottom: calc(100% + 10px);
    left: 50%; transform: translateX(-50%) translateY(4px);
    background: #171e28;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 11px;
    white-space: nowrap;
    box-shadow: 0 10px 24px rgba(0,0,0,0.5);
    opacity: 0; pointer-events: none;
    transition: opacity 140ms ease, transform 140ms ease;
    z-index: 10;
  }}
  .pn-tooltip strong {{ display: block; color: #fff; font-size: 12px; margin-bottom: 1px; }}
  .pn-tooltip span {{ color: var(--text-secondary); }}
  .player-node:hover .pn-tooltip {{ opacity: 1; transform: translateX(-50%) translateY(0); }}

  /* ── legend ──────────────────────────────────────────────────── */
  .board-legend {{
    display: flex; flex-wrap: wrap; gap: 16px;
    margin-top: 14px; padding: 0 4px;
  }}
  .legend-item {{
    display: flex; align-items: center; gap: 6px;
    font-size: 11px; color: var(--text-secondary); font-weight: 500;
  }}
  .legend-dot {{ width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }}
</style>
</head>
<body>
  <div class="board-wrap" id="board-wrap">
    <div class="board-header">
      <div>
        <p class="board-title">{safe_title}</p>
        {f'<div class="board-subtitle-pill"><span class="dot"></span>{safe_subtitle}</div>' if safe_subtitle else ''}
      </div>
      <div class="board-brand">⚽🇲🇦 ATLAS</div>
    </div>

    <div class="board-toolbar">
      <div class="tb-btn" id="zoom-out" title="Zoom out">&#8722;</div>
      <div class="tb-zoom-label" id="zoom-label">100%</div>
      <div class="tb-btn" id="zoom-in" title="Zoom in">&#43;</div>
      <div class="tb-btn" id="zoom-reset" title="Reset view">&#8634;</div>
      <div class="tb-btn" id="tilt-toggle" title="Toggle broadcast camera angle">&#127909;</div>
      <div class="tb-spacer"></div>
      <div class="tb-hint show-hint" id="pan-hint">drag to pan when zoomed</div>
      <div class="tb-btn" id="fullscreen-btn" title="Maximize">&#9974;</div>
    </div>

    <div class="pitch-viewport" id="pitch-viewport">
      <div class="pitch-shell" id="pitch-shell">
        {_pitch_markings_svg()}
        {_tactical_spine_svg(assigned_players)}
        {''.join(cards)}
      </div>
    </div>

    <div class="board-legend">{legend_html}</div>
  </div>

<script>
(function() {{
  var wrap = document.getElementById('board-wrap');
  var viewport = document.getElementById('pitch-viewport');
  var shell = document.getElementById('pitch-shell');
  var zoomLabel = document.getElementById('zoom-label');
  var zoom = 1, panX = 0, panY = 0;
  var MIN_ZOOM = 0.6, MAX_ZOOM = 2.2, STEP = 1.2;

  function apply() {{
    shell.style.setProperty('--zoom', zoom);
    shell.style.setProperty('--pan-x', panX + 'px');
    shell.style.setProperty('--pan-y', panY + 'px');
    zoomLabel.textContent = Math.round(zoom * 100) + '%';
  }}
  function clampPan() {{
    var maxPan = Math.max(0, (zoom - 1) * viewport.clientWidth * 0.5);
    panX = Math.max(-maxPan, Math.min(maxPan, panX));
    var maxPanY = Math.max(0, (zoom - 1) * viewport.clientHeight * 0.5);
    panY = Math.max(-maxPanY, Math.min(maxPanY, panY));
  }}
  function setZoom(z) {{
    zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));
    clampPan();
    apply();
  }}

  document.getElementById('zoom-in').addEventListener('click', function() {{ setZoom(zoom * STEP); }});
  document.getElementById('zoom-out').addEventListener('click', function() {{ setZoom(zoom / STEP); }});
  document.getElementById('zoom-reset').addEventListener('click', function() {{ zoom = 1; panX = 0; panY = 0; apply(); }});
  document.getElementById('tilt-toggle').addEventListener('click', function() {{
    viewport.classList.toggle('tilted');
    this.style.background = viewport.classList.contains('tilted') ? 'rgba(212,175,55,0.22)' : '';
  }});
  viewport.addEventListener('wheel', function(e) {{
    e.preventDefault();
    setZoom(zoom * (e.deltaY < 0 ? 1.08 : 0.93));
  }}, {{ passive: false }});
  viewport.addEventListener('dblclick', function() {{ zoom = 1; panX = 0; panY = 0; apply(); }});

  var dragging = false, startX = 0, startY = 0, startPanX = 0, startPanY = 0;
  shell.addEventListener('mousedown', function(e) {{
    if (zoom <= 1) return;
    dragging = true;
    shell.classList.add('is-panning');
    startX = e.clientX; startY = e.clientY;
    startPanX = panX; startPanY = panY;
  }});
  window.addEventListener('mousemove', function(e) {{
    if (!dragging) return;
    panX = startPanX + (e.clientX - startX);
    panY = startPanY + (e.clientY - startY);
    clampPan();
    apply();
  }});
  window.addEventListener('mouseup', function() {{
    dragging = false;
    shell.classList.remove('is-panning');
  }});

  // Fullscreen / Maximize
  // ─────────────────────────────────────────────────────────────────
  // The real Fullscreen API is usually BLOCKED here: st.iframe renders
  // this inside a sandboxed iframe whose sandbox tokens don't include
  // "allow-fullscreen" (checked directly against the installed
  // Streamlit's own frontend bundle — see this file's module docstring),
  // so document.fullscreenEnabled is normally false no matter what this
  // script does. Real Fullscreen is still attempted first in case a
  // future Streamlit version or a different embedding context allows it;
  // when it's blocked, this falls back to actually RESIZING the real
  // <iframe> element on the page instead of a same-size overlay:
  // Streamlit's iframe component watches the embedded document's size via
  // postMessage and grows the iframe to match (see IFrame.*.js in the
  // installed package) — so setting a much bigger inline height here and
  // letting that mechanism report it genuinely enlarges the board on the
  // real page, not just inside an unchanged-size box.
  var fsBtn = document.getElementById('fullscreen-btn');
  var realFullscreenOK = document.fullscreenEnabled || document.webkitFullscreenEnabled;
  var maxed = false;
  var naturalHeight = null;

  function topViewport() {{
    try {{
      if (window.top && window.top !== window) {{
        return {{ w: window.top.innerWidth, h: window.top.innerHeight }};
      }}
    }} catch (e) {{ /* cross-origin — fall through */ }}
    return {{ w: window.innerWidth, h: window.innerHeight }};
  }}

  function enterMaximize() {{
    if (naturalHeight === null) naturalHeight = wrap.getBoundingClientRect().height;
    var vp = topViewport();
    var targetH = Math.max(naturalHeight, vp.h - 90);
    var overhead = wrap.querySelector('.board-header').getBoundingClientRect().height
      + wrap.querySelector('.board-toolbar').getBoundingClientRect().height
      + wrap.querySelector('.board-legend').getBoundingClientRect().height
      + 70;
    var pitchTargetH = Math.max(240, targetH - overhead);
    var pitchTargetW = pitchTargetH * (105 / 68);
    viewport.style.height = pitchTargetH + 'px';
    viewport.style.width = Math.min(pitchTargetW, vp.w - 80) + 'px';
    viewport.style.aspectRatio = 'unset';
    viewport.style.margin = '0 auto';
    wrap.classList.add('is-maxed');
    maxed = true;
    fsBtn.title = 'Restore';
    fsBtn.style.background = 'rgba(212,175,55,0.22)';
  }}

  function exitMaximize() {{
    viewport.style.height = '';
    viewport.style.width = '';
    viewport.style.aspectRatio = '';
    viewport.style.margin = '';
    wrap.classList.remove('is-maxed');
    maxed = false;
    fsBtn.title = 'Maximize';
    fsBtn.style.background = '';
  }}

  fsBtn.addEventListener('click', function() {{
    if (realFullscreenOK) {{
      var isFs = document.fullscreenElement || document.webkitFullscreenElement;
      if (!isFs) {{
        (wrap.requestFullscreen || wrap.webkitRequestFullscreen).call(wrap).catch(function() {{
          realFullscreenOK = false;  // request rejected despite the check — fall back from now on
          maxed ? exitMaximize() : enterMaximize();
        }});
      }} else {{
        (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      }}
    }} else {{
      maxed ? exitMaximize() : enterMaximize();
    }}
  }});
  document.addEventListener('fullscreenchange', function() {{
    wrap.classList.toggle('is-fullscreen', !!document.fullscreenElement);
  }});
  document.addEventListener('webkitfullscreenchange', function() {{
    wrap.classList.toggle('is-fullscreen', !!document.webkitFullscreenElement);
  }});

  apply();
}})();
</script>
</body>
</html>'''

    return doc, 640


def render_setup_panel_css() -> str:
    """
    Returns a <style> block (page-scoped — only affects the Tactical Board
    page's own DOM, including its sidebar, since it's injected once via
    st.markdown from that page file only, not from any shared module other
    pages import). Restyles native Streamlit widgets in the left setup
    column + this page's sidebar into the same dark/gold token system as
    render_pitch_html(), without touching dashboard/sidebar.py's Python
    (which 11 other pages also import) — purely a visual skin for this
    page's session.
    """
    return '''
    <style>
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #10151c 0%, #0a0d12 100%);
        border-right: 1px solid rgba(255,255,255,0.06);
    }
    [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.08); }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: radial-gradient(120% 140% at 15% -10%, #161c25 0%, #0d1117 60%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        padding: 4px 2px;
    }
    .tb-eyebrow {
        font-family: 'Barlow Condensed', sans-serif;
        font-weight: 700; font-size: 12px; letter-spacing: 0.14em;
        color: #d4af37; text-transform: uppercase; margin: 2px 0 10px 0;
    }
    </style>
    '''