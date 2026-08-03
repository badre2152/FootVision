# CHANGELOG — Football Atlas

This ledger exists specifically to defeat the **"fixes keep disappearing"**
pattern documented in `03_regression_protection.md`: confirmed, tested fixes
were found silently reverted in later delivered archives, with nobody
noticing until the next manual audit. From now on, every confirmed bug fix
is recorded here with the exact test that protects it, so a
possibly-stale/reverted archive can be checked against a known-good list in
seconds instead of re-auditing the whole codebase from scratch.

**How to use this file:** before trusting "this is fixed," grep this file
for the relevant filename/symbol, then run the named test(s) against the
CURRENT archive. If they fail, the fix has regressed.

---

## [Unreleased] — 2026-08-02

### Added

#### 5. Dashboard authentication / access control (`06_authentication.md`)
- **Files:** `dashboard/auth.py` (new), `dashboard/app.py`, all 11 files in
  `dashboard/pages/*.py`, `.streamlit/secrets.toml.example` (new, tracked),
  `.streamlit/secrets.toml` (new, gitignored dev placeholder),
  `.gitignore`, `tests/test_auth.py` (new).
- **Symptom:** `grep -rln "authenticate\|login\|password" dashboard/`
  returned zero results — the dashboard, including the interactive Squad
  Builder page that lets anyone reachable preview/change a candidate
  squad, had no access control at all before this session. Deployed
  anywhere beyond localhost (Render/Railway per `README.md`, or any
  shared network), anyone with the URL could view pre-announcement squad
  strategy or manipulate the builder.
- **Fix:** a single shared-password login gate, per the spec's minimal
  viable option. Implemented as ONE centralized module
  (`dashboard/auth.py::require_auth()`) imported and called at the top of
  every page — app.py and all 11 files in `dashboard/pages/` — immediately
  after `st.set_page_config()` and before `render_sidebar()` or any Gold
  data load. **Deliberately not copy-pasted per file**, even though the
  spec's own sample snippet is written to be duplicated into every page:
  this project's own changelog above documents its dominant recurring
  failure mode as logic re-typed independently in multiple places and then
  silently drifting/reverting between sessions (see `TOP_EXPORT_COLS`,
  entry #1 above) — applying that exact risk to the single most
  security-sensitive piece of code in the project would have been
  reckless. **Fails closed**: if `.streamlit/secrets.toml` is missing
  entirely, or exists but has no `APP_PASSWORD` key, access is blocked
  with a visible configuration error rather than silently granted — same
  "no silent failure" principle already applied throughout this codebase
  (see `docs/architecture.md`). The typed password is deleted from
  `st.session_state` immediately after the comparison, not left sitting
  around. `.streamlit/secrets.toml` (the real, gitignored file) ships with
  a placeholder password and a comment demanding it be changed before any
  non-localhost deployment; `.streamlit/secrets.toml.example` is the
  tracked template.
- **Explicitly NOT implemented (spec's own "Better version" section, out
  of scope for this pass):** per-user credentials and an access log for
  accountability. A single shared password gives no per-user audit trail
  of who viewed or changed what — flagged here per this project's
  explicit-disclosure convention, not silently left unmentioned. Needed
  if more than one staff member requires distinguishable access.
- **Regression found and fixed in the same session:** adding the gate
  broke three pre-existing `AppTest`-based smoke tests that ran pages
  without an authenticated session (`tests/test_dashboard_post_hoc_banner.py`,
  `tests/test_final_squad_page.py`, `tests/test_player_analysis_page.py`)
  — they now hit the login screen instead of the content they were
  written to check. Fixed by setting
  `at.session_state["_auth_ok"] = True` before `at.run()` in each of
  those (8 call sites total), the same bypass a real authenticated
  session performs. This is the correct fix, not a workaround: those
  tests exist to check page CONTENT, and `tests/test_auth.py` (below) is
  now the dedicated test for the gate itself.
- **Protected by:** `tests/test_auth.py` — 17 tests: 5 unit tests on the
  password-check logic directly (correct/wrong password, missing
  `secrets.toml`, `secrets.toml` without the key, and a falsy/blank
  configured password — all four "fails closed" cases), plus 12
  `AppTest`-parametrized tests, one per real page file (`app.py` + all 11
  `dashboard/pages/*.py`, discovered from disk so a future new page file
  is automatically covered), each asserting the spec's literal
  Acceptance Criterion: no exception, only the gate's own title renders,
  zero `st.dataframe` elements, and a password field is present.
- **Verified:** `MINIO_MOCK=1 pytest tests/test_auth.py -q` → 17 passed.
  Full suite (`MINIO_MOCK=1 pytest tests/ -q --ignore=tests/test_sentiment_analyzer.py`)
  → 400 passed, 9 failed — all 9 pre-existing and unrelated to this
  change (6 already documented under "Known issues" for the 2026-08-01
  session below; 3 newly discovered this session while re-running the
  full suite, documented under this session's own "Known issues" below).
  Confirmed none of the 9 involve `dashboard/auth.py`, `_auth_ok`, or
  `session_state` in their failure output.
- **Not verified:** a real browser session against a deployed instance
  (Render/Railway) with a non-placeholder `APP_PASSWORD` — not possible
  from this sandboxed environment; the placeholder password in
  `.streamlit/secrets.toml` MUST be changed before any such deployment.

#### 6. Tactical Board redesign — premium HTML/CSS pitch (design request, no numbered spec file)
- **Files:** `dashboard/pitch_renderer_html.py` (new), `dashboard/pages/6_tactical_board.py`.
- **Symptom:** the Tactical Board's pitch was a plain matplotlib/mplsoccer
  image (`st.pyplot`) — flat colored circle markers, no hover states, no
  entrance animation, no gradients or depth. Requested redesign to a
  "premium football analytics platform" look (UEFA/Sofascore/FotMob-style
  lineup graphics) while keeping formation math and XI selection
  unchanged.
- **Root constraint:** a matplotlib figure is a static raster/vector
  image — no styling of it can add CSS gradients, hover states, or
  animation, full stop. Confirmed with the user before building (see
  chat) that the rendering TECHNOLOGY had to change (HTML/CSS component
  instead of `st.pyplot`) to satisfy the brief, while
  `dashboard/pitch_renderer.py`'s `assign_xi_to_formation()`, `FORMATIONS`,
  and `render_pitch()` stay completely untouched — new file, not an edit,
  specifically so the 19 tests in `tests/test_pitch_renderer.py` that
  protect that module needed zero changes.
- **Coordinate-system correction found along the way:** this project's own
  `FORMATIONS` comment described the pitch as "0-100 (length) x 0-80
  (width)". Checked directly against the installed `mplsoccer` version
  (`Pitch(pitch_type="opta").dim`) — the real coordinate space is 0-100 x
  0-100 (normalized on both axes), with the true 105m x 68m pitch shape
  applied by mplsoccer only at draw time, not baked into the coordinate
  values. `dashboard/pitch_renderer_html.py` uses the corrected 0-100 x
  0-100 mapping (`left% = x`, `top% = y`) so the new renderer reproduces
  the *exact same* visual layout mplsoccer already produced, not a
  distorted one. `FORMATIONS`' actual coordinate values were not touched
  — only this module's understanding of how to map them to CSS percentages.
- **Fix:** `render_pitch_html()` takes the same assigned-slot data
  `render_pitch()` always took and returns a self-contained HTML document
  — dark radial-gradient panel, a mowed-stripe grass pitch (CSS
  `repeating-linear-gradient`) with SVG markings (boundary, halfway line,
  center circle, both penalty/six-yard boxes, penalty spots — sized as
  percentages of real pitch dimensions so they render as true
  circles/rectangles once the container's `aspect-ratio: 105/68` stretches
  the 0-100 coordinate space into the real pitch shape), circular player
  photos with a position-colored gradient ring (base64-embedded via
  `find_player_photo()` — reused, not reimplemented, so both renderers
  agree on "does this player have a photo"), a staggered fade+rise-in
  entrance animation per player, and a hover tooltip (name/position/club/
  DSSScore). Players without a photo get a gradient+initials badge instead
  of a plain colored circle. `dashboard/pages/6_tactical_board.py`'s XI
  selection logic (`slot_counts`, "Best XI by DSSScore" pooling,
  "Rotation Strategy match" handling, the `assign_xi_to_formation()` call
  itself) is byte-identical to the pre-redesign version — diffed to
  confirm before considering this done; only imports, the `require_auth()`
  call (spec #6, entry #5 above), setup-panel container styling, and the
  final render call changed.
- **Deprecation caught during testing:** the redesign was originally built
  on `st.components.v1.html` per the initial plan, but a live AppTest run
  against the installed Streamlit surfaced `st.components.v1.html will be
  removed after 2026-06-01` — already past due on today's date. Switched
  to `st.iframe()` (current API, and actually better here: `height="content"`
  auto-sizes to the real content height instead of a guessed pixel value)
  with a `hasattr(st, "iframe")` fallback to `components.html()` for an
  older Streamlit install that predates it.
- **Not verified:** no headless browser was available in the sandbox this
  was built in to screenshot the actual rendered pitch — verified the
  generated HTML is well-formed (no broken template braces, valid CSS) and
  that the page runs end-to-end with zero exceptions via AppTest, but the
  visual result (spacing, gradients, animation timing) needs a real look
  in a browser before considering this finished. A standalone preview
  file was generated and handed to the user directly for that check.
- **Protected by:** existing `tests/test_pitch_renderer.py` (19 tests, all
  passing unmodified — proof the matplotlib path is untouched). No new
  automated test added specifically for `pitch_renderer_html.py`'s visual
  output (HTML/CSS correctness isn't meaningfully assertable without a
  real browser); manually verified via a synthetic-data AppTest run
  (11-player XI, mixed photo/no-photo) that the page produces zero
  exceptions end-to-end.
- **Verified:** `MINIO_MOCK=1 pytest tests/ -q --ignore=tests/test_sentiment_analyzer.py`
  → 400 passed, 9 failed — identical failure set to entry #5 above (all
  pre-existing, all unrelated).

#### 7. Tactical Board v2 — zoom/pan/fullscreen, tactical spine, legend (follow-up design request)
- **Files:** `dashboard/pitch_renderer_html.py` (extended — same file from
  entry #6, no new file needed).
- **Symptom:** entry #6 shipped the premium HTML/CSS pitch but had no way
  to inspect it more closely or view it larger — user explicitly asked for
  it to be "minimized and upscaled" and for more visual polish/ideas.
- **Fix, in one file, `render_pitch_html()` extended (still zero change to
  `assign_xi_to_formation()`/`FORMATIONS`/`render_pitch()`):**
  - **Zoom + pan + fullscreen toolbar.** `transform: scale()` inside a
    fixed-`aspect-ratio` `overflow: hidden` viewport, so zooming never
    changes the outer box size Streamlit measured for `st.iframe`'s
    `height="content"` — only what's drawn inside it does. Mouse-wheel
    zoom, click +/−/reset buttons, drag-to-pan once zoomed past 100%
    (clamped so the pitch can't be dragged fully out of view),
    double-click to reset. Fullscreen uses the standard Fullscreen API on
    the whole board card, with `document.fullscreenEnabled` feature
    detection — the button hides itself instead of doing nothing on click
    if the embedding context doesn't support it.
  - **Tactical spine.** A thin glowing gold line connecting each present
    formation line's centroid (GK → DEF → MID → FWD), computed in Python
    from the exact same `x`/`y` `assign_xi_to_formation()` already
    produced — a broadcast-graphic touch that reads the team shape at a
    glance without a line to every individual player (would be visual
    noise for an 11-node XI).
  - **Position legend** (color key strip under the pitch) and a small
    position-letter chip (GK/D/M/F) on every node — position is now
    readable at a glance without needing to hover, not just in the
    tooltip.
  - **Floodlight corner glow + film-grain overlay.** Two soft radial
    highlights at the top corners (stadium-lighting feel) and a
    near-invisible (5% opacity, `mix-blend-mode: overlay`) SVG
    `feTurbulence` grain texture — the "premium lighting/depth" half of
    the original brief that entry #6 under-delivered on with only a flat
    two-stop gradient.
- **Verified:** regenerated the standalone preview file and re-ran the
  same synthetic-data AppTest smoke test used for entry #6 — zero
  exceptions. `MINIO_MOCK=1 pytest tests/ -q --ignore=tests/test_sentiment_analyzer.py`
  → 400 passed, 9 failed, identical failure set to entries #5/#6 (still
  all pre-existing, still all unrelated). Sanity-checked the generated
  HTML for balanced tags and no leftover unescaped `{{`/`}}` from the
  Python f-string templating (a real risk with this much CSS in an
  f-string — checked for it directly rather than assuming).
- **Not verified:** same limitation as entry #6 — no headless browser
  available in this sandbox to actually see the zoom/pan/fullscreen
  interactions or the grain/glow rendering. A second standalone preview
  file was generated and handed to the user directly.

#### 8. Tactical Board v3 — club badges, formation pill, broadcast-angle toggle (second follow-up)
- **Files:** `dashboard/pitch_renderer_html.py` (extended again — same
  file as entries #6/#7).
- **Symptom:** user asked to pull more specifically from the 4 reference
  images they'd shared (a tactical-pitch dashboard, a Champions League
  "Team of the Week" graphic, a PSG/Inter combined-XI graphic, and a
  Spain national-team Best XI graphic).
- **What was actually reusable from those 4 images, and what wasn't:**
  - Two of the four (the UCL and PSG/Inter graphics) show a small club
    badge overlapping each player photo. **Real club crests are
    trademarked logos this project has no rights to reproduce** — so
    instead of pulling in actual crest images, added `_club_monogram()`:
    a small generated text badge (e.g. "Paris Saint-Germain" → "PSG",
    "West Ham" → "WH") in the same visual position (top-left, overlapping
    the photo ring) the reference images use for a real crest. Same
    visual read, no IP problem, and no new data dependency — reuses the
    `club` field already flowing through the page.
  - Stronger typographic hierarchy across all 4 images (bold condensed
    titles, an accent bar/pill under the headline) — added a gold accent
    underline beneath the board title and turned the formation subtitle
    into a pill badge instead of plain text.
  - **What was NOT actually in the reference images, flagged explicitly
    rather than silently added:** none of the 4 are tilted/isometric —
    all are flat top-down or straight-on graphics. A 3D "broadcast camera
    angle" toggle was added anyway as an extra idea (common in some other
    broadcast lineup graphics this project's brief was inspired by), but
    kept **off by default and toggle-able**, specifically because it
    isn't something the user's own references actually showed — a forced
    permanent change on that basis would have been overreach. Implemented
    as a single `perspective()`/`rotateX()` transform on the whole
    `.pitch-viewport` (markings + players together as one flat plane, not
    tilted independently), so nothing shifts out of alignment with the
    percentage-based player coordinates when toggled — a naive
    per-layer tilt would have needed real per-point 3D projection math to
    stay aligned, which isn't verifiable without a browser to check
    against (see "Not verified" below), so the whole-viewport approach
    was chosen specifically to avoid shipping something that might
    silently misalign.
- **Verified:** `MINIO_MOCK=1 pytest tests/ -q --ignore=tests/test_sentiment_analyzer.py`
  → 400 passed, 9 failed, identical failure set to entries #5–#7 (still
  all pre-existing, still all unrelated). Sanity-checked the generated
  HTML for balanced tags, no leftover unescaped `{{`/`}}`, and confirmed
  the club-monogram logic produces the expected output on real club-name
  strings (e.g. "Paris Saint-Germain" → "PSG").
- **Not verified:** same limitation as entries #6/#7 — no headless
  browser in this sandbox to see the club badges, title pill, or tilt
  toggle actually rendered. A third standalone preview file was generated
  and handed to the user directly for that check.

#### 9. Tactical Board — working "fullscreen" via resize fallback (fullscreen button fix)
- **Files:** `dashboard/pitch_renderer_html.py` (extended again).
- **Symptom:** the fullscreen button added in entry #7 wasn't visibly
  doing anything for the user.
- **Root cause, found by actually reading Streamlit's own installed
  frontend bundle rather than guessing:** `st.iframe()` renders the
  embedded HTML inside a *sandboxed* iframe. Checked
  `streamlit/static/static/js/IFrameUtil.*.js` directly in the installed
  package — the `sandbox` attribute it sets is
  `allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox
  allow-same-origin allow-scripts allow-downloads`. **No `allow-fullscreen`
  token.** Per the WHATWG sandbox spec, that blocks the real Fullscreen
  API from inside the iframe regardless of the `allow="...fullscreen..."`
  Permissions-Policy attribute also being set on the same iframe (a common
  gotcha — the two are separate mechanisms, and both have to allow it).
  Confirmed directly: `document.fullscreenEnabled` is `false` in this
  context, which is exactly what entry #7's own feature-detection was
  correctly reading — the button was quietly disabling itself as designed,
  not malfunctioning.
- **Fix:** since `st.iframe`'s own component (same bundle,
  `IFrame.*.js`) already auto-resizes the real `<iframe>` element on the
  page to match the embedded document's measured size (via a
  `postMessage`-based `MutationObserver`, for `height="content"` mode),
  clicking the button now — when real Fullscreen is unavailable, which is
  the normal case here — sets a much larger inline height/width on the
  pitch viewport instead (computed from `window.top.innerWidth/Height`,
  reachable because `allow-same-origin` IS in the sandbox list, with a
  same-origin-access try/catch fallback to `window.innerHeight` if that
  ever isn't true). That resize is a real DOM mutation, the existing
  auto-resize script picks it up, and the actual `<iframe>` on the
  rendered Streamlit page grows to match — this genuinely enlarges the
  board on the page, it isn't a same-size overlay pretending to be bigger.
  Real `requestFullscreen()` is still attempted FIRST (with a promise
  `.catch()` that falls back to the resize approach if a future
  Streamlit/embedding context reports `fullscreenEnabled: true` but still
  rejects the actual request), so this upgrades automatically if
  Streamlit ever adds the sandbox token.
- **Disclosed limitation, not silently smoothed over:** this is a
  "maximize within the page" experience, not true OS-level fullscreen —
  the browser chrome and (depending on where the iframe sits in the page)
  Streamlit's own sidebar/header may still be visible around it. That's
  the honest ceiling of what's achievable from inside a sandboxed iframe
  without a `allow-fullscreen` token Streamlit's own component doesn't
  set, not a corner that was cut.
- **Verified:** extracted the actual `<script>` content and ran
  `node --check` (valid syntax), then actually EXECUTED it against a real
  DOM via `jsdom` (not just read the code) — dispatched clicks on every
  toolbar control (zoom in/out/reset, tilt toggle, maximize, restore) and
  confirmed each one produces the expected state with zero runtime
  exceptions, including specifically the maximize→restore round-trip
  (inline `width`/`height` are set on click, and fully cleared back to
  empty string on the second click, confirmed by reading
  `viewport.style.height` before/after both clicks — not just assuming
  the restore branch runs correctly).
  `MINIO_MOCK=1 pytest tests/ -q --ignore=tests/test_sentiment_analyzer.py`
  → 400 passed, 9 failed, identical failure set to entries #5–#8.
- **Not verified:** the actual on-page enlarging effect and visual
  quality of the resulting layout — jsdom confirms the JS logic and DOM
  state changes are correct, but not what it looks like in a real browser
  against a real Streamlit-rendered page. A fourth preview file was
  generated and handed to the user directly.

### Known issues found during this session (NOT fixed — out of scope of
### `06_authentication.md`, flagged for a future dedicated fix)
- **`tests/test_dashboard_post_hoc_banner.py::test_post_hoc_banner_present_on_retrospective_pages[dashboard/pages/5_selection_audit.py]`**
  fails on this archive: `No selection_audit_*.json found in gold`. This
  test depends on a `gold/selection_audit_*.json` fixture file that isn't
  present among the committed `gold/*.csv`/`.json` files in this delivery
  — a missing-fixture / data-availability gap, unrelated to authentication.
- **`tests/test_final_squad_page.py::TestFinalSquadPageSmokeTest::test_replacement_expander_is_rendered_per_selected_player`**
  and **`::test_page_degrades_gracefully_when_top26_lacks_player_id`** both
  fail. Root cause: these tests (written per an older `10_depth_chart.md`
  Implementation Plan step, per the test file's own docstring) assert
  every `st.expander` on `dashboard/pages/4_final_squad.py` is labeled
  `"🔄 Replacement options"`. That expander no longer exists anywhere in
  `dashboard/` (`grep -rn "Replacement options" dashboard/` → zero
  matches) — the page's only expander today is `"📝 Coach note"`, added by
  entry #4 (`20_coach_notes.md`) above. The "Replacement options" feature
  was apparently removed or renamed at some point after these tests were
  written, and the tests were never updated to match — pre-existing
  test/implementation drift, confirmed unrelated to this session's auth
  change (the page runs with zero exceptions; the failures are purely
  about expander labels/counts). Deserves its own dedicated fix: either
  the replacement-candidate feature needs re-implementing on this page, or
  these two tests need rewriting for what the page actually does now.

---

## [Unreleased] — 2026-08-01

### Fixed

#### 0. `01_temporal_cutoff.md` — DECISION_DATE enforcement REVERTED then RE-APPLIED
- **Files:** `config.py`, `ingestion/loaders/transfermarkt_injury_loader.py`,
  `ingestion/loaders/clubelo_loader.py`, `run_bronze.py`, `run_sofifa.py`,
  `run_fotmob.py`, `run_sd_extractor.py`, `diagnose_clubelo_coverage.py`,
  `tests/test_temporal_cutoff.py`
- **Symptom:** an earlier session confirmed-fixed and tested (4/4 passing) a
  full implementation of `01_temporal_cutoff.md` across all 8 files above.
  A later delivered archive (this one, pre-merge) had **7 of those 8 files
  silently reverted to their pre-fix state** — `transfermarkt_injury_loader.py`
  and `clubelo_loader.py` were back to filtering on `datetime.now()`/
  `date.today()` instead of a fixed decision date, `--as-of` had vanished
  from all four Bronze entrypoints, and `tests/test_temporal_cutoff.py` was
  gone entirely. This is the exact "fixes keep disappearing" pattern this
  file exists to catch — caught here by a manual diff against the last known
  confirmed-fixed archive, not by an automated check (none existed for this
  spec at the time).
- **Compounding bug found during re-merge:** the one file that DID survive
  (`config.py`'s `DECISION_DATE`, touched by a separate, narrower
  `04_injury_predictor_temporal_fix.md` session) had been changed to
  `date(2026, 6, 1)` — a date factually AFTER the real WC2026 squad
  announcement (`2026-05-26`, sourced in
  `analytics/ml/selection_classifier.py`). A decision-cutoff date set after
  the decision was made defeats the entire purpose of the spec. Corrected
  back to `date(2026, 5, 25)` (the day before the announcement).
- **Fix:** re-applied the full `01_temporal_cutoff.md` implementation on top
  of this archive's other, unrelated confirmed fixes (SquadRank export,
  SoFIFA upload shape, chemistry transparency, coach notes, concurrency
  docs) — nothing else in this archive was touched or lost.
- **Protected by:** `tests/test_temporal_cutoff.py` (4 tests). **NOT YET**
  added to `tests/test_regression_protection.py` — see "Known issues" below;
  this is flagged as a gap in this delivery's own safety net, not silently
  left unmentioned.
- **Verified:** `MINIO_MOCK=1 pytest tests/test_temporal_cutoff.py
  tests/test_injury_predictor.py tests/test_clubelo_loader.py -q` → 56
  passed (0 regressions from the re-merge). Full pipeline re-run against
  live network sources (transfermarkt.com, api.clubelo.com) to confirm
  `inj_last_date.max() <= DECISION_DATE` on a fresh Gold export is **still
  outstanding** — not possible from a network-sandboxed environment; must be
  run manually.

#### 1. `SquadRank` missing from `top26_*.csv` export
- **File:** `run_gold.py`
- **Symptom:** `squad_df` had a `SquadRank` column (1–26, squad position)
  computed correctly by `select_squad_with_position_cap()`, but the
  inline `top_cols` list used to write `top26_*.csv` never included it —
  so the exported file only ever showed the global 1–49 `Rank`, never the
  squad-relative position. This is the exact ambiguity flagged as a
  finding in `AUDIT_AND_FIXES.md` ("consider adding a `SquadPosition`
  column"). Confirmed previously fixed in an earlier session, then found
  reverted in a later delivered archive.
- **Root cause:** the export column list was re-typed inline inside
  `main()` on every session instead of living in one named, importable
  place — nothing stopped a later edit from silently dropping back to an
  older version of that list.
- **Fix:** extracted the list to a module-level constant
  `TOP_EXPORT_COLS` and a pure function `build_top_cols(available_columns)`
  that filters it against whatever columns actually exist on the squad
  DataFrame. `main()` now calls `build_top_cols()` instead of re-typing
  the list. `"SquadRank"` is listed first, ahead of the global `"Rank"`.
- **Protected by:**
  `tests/test_regression_protection.py::TestTopColsIncludesSquadRank`
  (3 tests — presence, graceful-absence, and actual DataFrame selection).
- **Verified:** reverting `TOP_EXPORT_COLS` back to the old list (without
  `SquadRank`) makes 2/3 of these tests fail immediately — confirmed by
  deliberately re-introducing the bug and re-running the suite before
  shipping this fix.

#### 2. `run_sofifa.py` MinIO upload used the wrong (wrapped) JSON shape
- **Files:** `run_sofifa.py`, `ingestion/loaders/sofifa_loader.py` (dead
  code with the identical bug, fixed defensively — see "Also fixed" below)
- **Symptom:** zero `sofifa_*` columns ever reached Silver/Gold, with no
  error or warning anywhere in the pipeline. `MentalScore` for every
  player silently fell back to the position-median imputation instead of
  using real SoFIFA attribute data.
- **Root cause:** `run_sofifa.py` uploaded
  `{"run_date": ..., "players": results}` (a dict) to
  `s3://football-bronze/sofifa/...` instead of the flat `results` list
  that every other Bronze loader uses (`wikidata_loader.py`,
  `fbref_loader.py`, `run_fotmob.py`, etc. all call
  `upload_json(BUCKET, name, records)` with a flat list).
  `ingestion/silver_pipeline.py::join_all_bronze_sources()._read()` does
  `pd.DataFrame(read_latest_json(...))` and requires a `player_id` column
  to proceed with the merge. A flat list of per-player dicts produces
  that column correctly; the wrapped dict instead produces a DataFrame
  built from the dict's own top-level keys (`run_date`, `players`) — no
  `player_id` column exists, so `_read()` returns `None`, the merge is
  silently skipped, and the source is dropped with only a `logger.warning`
  that's easy to miss in a long pipeline run.
  Confirmed previously fixed, then found reverted in a later archive.
- **Fix:** extracted a pure function `build_minio_upload_payload(results)`
  that returns `results` unchanged, with a docstring explaining exactly
  why the shape matters. The MinIO upload call site now passes
  `build_minio_upload_payload(results)`. The local debug file on disk
  (`bronze/sofifa/players_sofifa_*.json`) intentionally keeps the
  human-readable `{"run_date": ..., "players": [...]}` wrapper — it is
  never read by the pipeline, only the MinIO object matters.
- **Also fixed (defensive, same bug class, not explicitly named in the
  spec but the identical footgun):** `ingestion/loaders/sofifa_loader.py`
  had the exact same wrapped-payload bug. This file is currently unused
  dead code (not imported/called anywhere, including `run_bronze.py`),
  but was fixed to the same flat-list convention so it can't silently
  reintroduce this bug if it's ever wired back in.
- **Protected by:**
  `tests/test_regression_protection.py::TestSofifaUploadFormat` (3 tests):
  - asserts `build_minio_upload_payload()` returns a flat list, not a dict
  - reproduces `silver_pipeline`'s exact `pd.DataFrame(...)` /
    `"player_id" in df.columns` check against both shapes, proving the
    flat shape works and the wrapped shape doesn't
  - drives `main()` end-to-end (Playwright, the seed CSV read, and
    `upload_json` all mocked) and asserts the actual object handed to
    `upload_json()` at the real call site is a flat list
- **Verified:** re-wrapping the payload (both at the function level and
  by simulating the old call-site code) makes 2/3 of these tests fail
  immediately — confirmed by deliberately re-introducing the bug and
  re-running the suite before shipping this fix.

### Added
- `tests/test_regression_protection.py` — regression tests for both fixes
  above, per `03_regression_protection.md` Functional Requirement #1
  ("every confirmed bug fix must ship with a regression test that fails
  loudly if the fix is undone").
- `.github/workflows/tests.yml` — minimal CI: runs `pytest tests/ -q`
  (with `MINIO_MOCK=1`) automatically on every push and pull request, per
  Functional Requirement #2.
- This `CHANGELOG.md` file itself, per Functional Requirement #3.
- `playwright-stealth` added to `requirements.txt` — it was imported
  directly by `run_sofifa.py` but missing from the dependency list, so a
  clean `pip install -r requirements.txt` could never actually run this
  script. Unrelated to the two regressions above but found during this
  session's verification pass; a one-line, behavior-neutral fix.

#### 3. ChemistryScore had no real-vs-imputed transparency flag (`19_chemistry_transparency.md`)
- **Files:** `ingestion/silver_pipeline.py`, `dashboard/pages/3_chemistry.py`
- **Symptom:** `chem_avg_per90` is 49/49 non-null in the final Gold export
  (after Silver's position-group median imputation), but only 11/49
  candidates ever had a real StatsBomb pairwise value — and there was no
  column anywhere marking which was which, unlike `PressureIndex`
  (`pressure_data_confidence`, reconstructed live in
  `analytics/set_piece_analyzer.py::clutch_ranking()`) or `MentalScore`
  (`sb_mental_data_real`) elsewhere in this codebase's own culture of
  flagging imputation.
- **Fix:** `join_chemistry_pairs()` now sets
  `df["chem_is_real"] = df["chem_avg_per90"].notna()` immediately after
  the pairwise merge, BEFORE `impute_by_position()` runs — the only point
  in the pipeline where real vs. imputed is still distinguishable. The
  flag is a plain boolean, not in `IMPUTE_OUTFIELD_COLS`/
  `IMPUTE_GK_COLS`, so it passes through Silver → Gold untouched.
  `dashboard/pages/3_chemistry.py` now has a second section ("ChemistryScore
  — real vs imputed") showing all 49 candidates, colored real=green
  (`#22c55e`) / imputed=grey (`#6b7280`) — same convention as the
  Pressure Index page — with a graceful fallback to `chem_num_partners >
  0` for older Gold files produced before this flag existed.
- **Protected by:** `tests/test_chemistry_transparency.py` — asserts the
  flag is set correctly before imputation, survives
  `impute_by_position()` unchanged, is `False` (never guessed `True`)
  when the Bronze source is missing, and counts DISTINCT players (not
  pair-rows).
- **Not verified end-to-end:** the acceptance criterion "sum(chem_is_real)
  ≈ 11/49" needs a live run against real MinIO/Bronze data
  (`run_silver.py` → `run_gold.py`) in an environment with network access
  to the MinIO server — only unit-level correctness is confirmed here.

#### 4. Coach/Staff Notes field (`20_coach_notes.md`)
- **Files:** `dashboard/notes_store.py` (new), `dashboard/pages/2_player_analysis.py`,
  `dashboard/pages/4_final_squad.py`, `dashboard/pages/11_squad_builder.py`,
  `dashboard/i18n.py`
- **Symptom:** the dashboard had no way for the coaching staff to record
  qualitative judgment (training-ground impressions, character/leadership
  notes) next to a player's data-driven scores — that knowledge
  structurally couldn't live anywhere in the tool.
- **Fix:** new `dashboard/notes_store.py` — a small, atomically-written
  JSON store (`data/coach_notes.json`) keyed by `player_id`, deliberately
  living in its own top-level `data/` directory (sibling to, never nested
  under, `bronze/`/`silver/`/`gold/`) so it is architecturally impossible
  for a `run_bronze.py`/`run_silver.py`/`run_gold.py` re-run to touch it.
  `get_note()`/`save_note()`/`get_all_notes()` per the spec's exact
  function names. "Last write wins" on concurrent edits is documented
  explicitly in the module docstring (per the spec's Edge Cases section),
  not left undefined. Wired into `2_player_analysis.py` (full
  read/write UI: text area + author field + "last edited by X at Y"
  caption) and, as compact **read-only** views, into `4_final_squad.py`
  (fully localized EN/FR/AR via new `final_squad.note_*` i18n keys) and
  `11_squad_builder.py` (which uses `player_name` as its id_col
  throughout — notes are looked up via `players_full`'s real `player_id`,
  joined back on `player_name`).
- **Protected by:** `tests/test_notes_store.py` — round-trip persistence,
  last-write-wins, corrupt-file resilience, and an explicit test proving
  `notes_store.DATA_DIR` is neither `GOLD_DIR` nor `BRONZE_DIR` and that
  a note survives a simulated `gold/` wipe-and-rewrite.
- **Not verified end-to-end:** the full acceptance criterion ("note
  persists after closing and reopening the dashboard", "survives a real
  `run_bronze.py` → `run_gold.py` run") needs an actual Streamlit session
  and a live pipeline run in an environment with Streamlit/MinIO
  installed — only the underlying storage logic is unit-tested here.

#### 5. Concurrency limitations were undocumented (`21_concurrency_docs.md`)
- **Files:** `docs/architecture.md` (new), `README.md`, `dashboard/notes_store.py`
- **Symptom:** the project's storage layer (flat CSV/JSON on MinIO/local
  disk, no locking, no transactions) had this constraint nowhere written
  down — an implicit, undocumented risk rather than an explicit, reasoned
  decision, per the spec's framing.
- **Fix:** documentation-only, per the spec's own scope (no code changes
  required). New `docs/architecture.md` states the limitation plainly,
  explains why it's a non-issue at today's single-operator scale, gives a
  concrete **trigger condition** for revisiting it (spec `#6` auth
  enabling multiple simultaneous users; concurrent pipeline re-runs;
  concurrent coach-note edits — the exact scenario spec `#20`'s Edge
  Cases section already called out), and names the upgrade path (SQLite
  with WAL mode for coach notes) without implementing it yet.
  `README.md`'s "Known limitations / roadmap" section now links to it.
  `dashboard/notes_store.py`'s existing "last write wins" docstring now
  cross-references it too, per the spec's Implementation Plan step 2
  (connect spec `#20` and `#21` explicitly).
- **Protected by:** `tests/test_concurrency_docs.py` — asserts the doc
  file exists, states the limitation and a trigger condition, and that
  both `README.md` and `notes_store.py` actually link to it (so the
  cross-references can't silently rot).

#### 6. No caching on dashboard data loading (`22_dashboard_caching.md`)
- **Files:** `dashboard/data_loader.py`, `tests/conftest.py` (new),
  `tests/test_dashboard_data_loader.py`, `tests/test_dashboard_caching.py` (new)
- **Symptom:** every `load_latest_*()` call in `data_loader.py` re-read
  CSV/JSON from disk on every single Streamlit rerun (i.e. every widget
  interaction) — invisible at 49 rows, but pure redundant I/O with no
  benefit, exactly as the spec's `grep -c "st.cache_data"` check showed
  (zero matches anywhere in the dashboard).
- **Fix:** `@st.cache_data(ttl=300)` added to every `load_latest_*()`
  function, plus `load_all_players_scored_history()` (spec #18's snapshot
  loader — reads EVERY Gold snapshot on every call, which is heavier than
  any single `load_latest_*()` and squarely matches the spec's Objective
  even though it's outside the literal `load_latest_*` naming — called
  out explicitly as a deliberate scope extension). `data_loader.py` now
  imports `streamlit`, which changes its original "ZERO Streamlit
  imports" design note (from when this module was added, 2026-07-14) — verified manually first that `st.cache_data`
  works correctly outside a live app ("bare mode": a harmless stderr
  warning, correct caching and return values) before relying on that.
  The 5-minute TTL means a fresh `run_gold.py` output can take up to 5
  minutes to appear — an explicit, documented v1 tradeoff per the spec's
  own instruction, not presented as a perfect solution.
- **Regression found and fixed during this same session (not a separate
  issue — direct consequence of this change):** because these cached
  functions take no arguments, Streamlit collapses the cache to ONE
  global slot per function, shared across the entire `pytest` process —
  not scoped to a test, a test file, or a monkeypatched `GOLD_DIR`. This
  broke `tests/test_final_squad_page.py` (a test expecting an EMPTY
  `gold/` dir was silently served another test's cached non-empty
  DataFrame instead of `None`) and could equally have corrupted
  `tests/test_dashboard_post_hoc_banner.py` (reads the real `gold/`
  fixture files) if it happened to run after a test that monkeypatched
  `GOLD_DIR` elsewhere. Fixed with a new **`tests/conftest.py`** —
  an autouse fixture that calls `st.cache_data.clear()` (the global
  clear, not a per-function one) before and after every test in the
  whole suite, rather than relying on each individual test file to
  remember to reset it. `test_dashboard_data_loader.py`'s `fake_dirs`
  fixture also explicitly clears the specific loaders it touches, as
  defense-in-depth on top of the global `conftest.py` fixture.
- **Protected by:** `tests/test_dashboard_caching.py` — proves a second
  call within the TTL window does not re-read disk (via a `pd.read_csv`
  call counter, following the spec's own suggested verification
  approach), that cached DataFrames are independent copies per call
  (mutating one caller's result can't corrupt another's), that
  `df.attrs["source_file"]` survives the cache copy, and that clearing
  the cache correctly picks up a newer file (the same mechanism a TTL
  expiry triggers internally).
- **Not verified end-to-end:** the real 5-minute wall-clock TTL expiry
  itself (would require either a 5-minute-long test or monkeypatching
  Streamlit's internal cache-timing implementation — neither is a stable
  choice, see that test file's own note on this) and behavior inside an
  actual running `streamlit run dashboard/app.py` session with real
  browser reruns — both need a live manual check per the spec's own
  Acceptance Criteria wording ("verify via a quick manual check").

#### 7. Rival/Opponent Player Benchmarking (`23_rival_benchmark.md`)
- **Files:** `analytics/rival_benchmark.py` (new),
  `seeds/rival_reference_players.csv` (new), `dashboard/pages/2_player_analysis.py`,
  `tests/test_rival_benchmark.py` (new), `tests/test_player_analysis_page.py` (new)
- **Symptom:** every comparison in this tool was Morocco-candidate-vs-
  Morocco-candidate — no way to ask "is our best CB actually competitive
  against real international-caliber opposition," exactly as the spec's
  own `grep` check showed (zero hits for "rival"/"benchmark" anywhere in
  `analytics/` or `dashboard/`).
- **Curation decision (spec's own Implementation Plan step 1 — explicitly
  a product decision, not purely technical):** 3 reference players from
  **Brazil** — Morocco's confirmed real Group C opponent (13 June 2026,
  MetLife Stadium), verified via web search against FIFA's own squad
  announcement — covering the spec's explicit minimum (GK, CB, ST):
  Alisson Becker (GK, Liverpool), Marquinhos (CB, PSG), Vinícius Júnior
  (FWD/ST — confirmed moved from left wing to central striker for this
  cycle per manager Carlo Ancelotti). This is a stronger, more defensible
  rationale than an arbitrary "best players in the world" pick — every
  one of these three is a player Morocco's squad will literally face.
- **⚠️ Known gap, disclosed rather than papered over:** the per-90 STAT
  VALUES for these 3 curated players are **not yet populated** —
  `stats_verified=False` for all 3 in `seeds/rival_reference_players.csv`,
  numeric columns blank. Precise per-90 numbers (Tkl_per90, GA_per90,
  etc., matching this project's own exact stat definitions) need a real
  FBref fetch to get right; approximating them from search-snippet
  percentages/rankings found during this session risked presenting
  plausible-looking but WRONG numbers as fact in a graduation project —
  worse than a visible gap. `compare_to_reference()` and the dashboard
  both surface `stats_verified` explicitly (faded radar trace + ⚠️ label
  + warning box) rather than silently plotting placeholder zeros as if
  real, following the exact same "flag it, don't hide it" pattern
  already established for `chem_is_real` (#19) and `is_pre_cutoff_fix`
  (#18) in this same codebase. **Next step for a real run:** populate the
  9 blank per-90 cells in `seeds/rival_reference_players.csv` from FBref
  for the 2024-2025 season (already set as `season_end_date`, safely
  pre-`DECISION_DATE`), then flip `stats_verified` to `True`.
- **Design decision — separate radar chart, not an added trace on the
  existing one:** the spec's Technical Requirements literally say "same
  visualization, additional trace for the reference player," but the
  existing Player Analysis radar plots `PerformanceScore`/`FormScore`/etc.
  — percentiles computed relative to the 49 Morocco candidates
  specifically (`percentile_score.py`). A rival player was never in that
  pool, so a trace on those exact axes would be comparing incompatible
  things. Built as a second, separate `go.Scatterpolar` radar instead —
  same chart TYPE and visual pattern, reused deliberately — plotting raw
  per-90 stat categories (`Gls_per90`, `Tkl_per90`, `GA_per90`, ...) that
  ARE comparable across any two players regardless of pool. Scaling for
  this chart is a fresh per-category min-max computed only across
  {this candidate, these references} — explicitly NOT the Morocco-pool
  normalization that feeds `DSSScore`, per the spec's Functional
  Requirement #3 (never feed this into scoring).
- **`DECISION_DATE` compliance (Functional Requirement #2):** every
  reference player's `season_end_date` (2025-05-25 for all 3, the
  2024-2025 European season) is asserted `< config.DECISION_DATE`
  (2026-05-25) by a dedicated test.
- **Merged 2026-08-02 with this session's separately-delivered spec `#12`
  (CSV/PDF export, `dashboard/report_utils.py`) — that work landed on
  `2_player_analysis.py` (the PDF/CSV export buttons at the bottom of the
  page) in parallel with this one. No conflict: this section was inserted
  above the export buttons; `build_player_profile_pdf()` was NOT modified
  to include the benchmark section (out of scope for spec #23) — flagged
  here as a natural follow-up if a coach wants the international
  reference comparison in the exported PDF too.**
- **Protected by:** `tests/test_rival_benchmark.py` (13 tests — position
  filtering, the GA_per90 inversion actually inverting, missing stats
  scaling to `None` not `0`, `stats_verified` surfacing correctly, and
  the `DECISION_DATE` compliance check above) and
  `tests/test_player_analysis_page.py` (3 AppTest smoke tests — this
  page had ZERO smoke-test coverage before this change, added given how
  much new UI landed on it). Re-ran the FULL suite after merging with
  spec `#12`'s changes: identical 9 pre-existing failures before and
  after (none new, none fixed) — confirmed via a clean pre-merge archive
  copy, same method used throughout this session.

### Known issues found during this session (NOT fixed — out of scope of
### `03_regression_protection.md`, flagged for a future dedicated fix)
- **`tests/test_regression_protection.py` itself is failing on THIS
  archive, pre-merge, on the two fixes it exists to protect:**
  `TestTopColsIncludesSquadRank` (3 tests) fails with
  `AttributeError: module 'run_gold' has no attribute 'build_top_cols'` —
  the function this changelog documents as added is simply absent from
  `run_gold.py` as delivered. `TestSofifaUploadFormat` (2 tests) fails
  because `run_sofifa.py` can't even import (`ModuleNotFoundError:
  playwright_stealth` — separate, unrelated env/dependency issue, not
  re-investigated here). **Confirmed via a clean checkout of this exact
  archive before any of this session's own changes** — i.e. this is not
  something introduced by the `01_temporal_cutoff.md` re-merge below; it
  was already true of the delivered archive. This means the "Pre-delivery
  checklist" at the bottom of this file (step 2: confirm
  `test_regression_protection.py` shows PASSED) was evidently not run, or
  its failure was not caught, before this archive was sent out — the exact
  failure mode this checklist exists to prevent, happening to the very
  session that added the checklist.
- `analytics/scoring/minutes_filter.py` is missing an `apply_dss_gate`
  function that `run_gold.py`'s pipeline order (per its own comments) and
  `tests/test_gold2_components.py::TestMinutesFilterWeightBinding::
  test_dssscore_actually_reads_config_weights` both expect to exist. This
  is itself a strong candidate for the exact "fixes keep disappearing"
  pattern (the function was referenced/documented as existing but is
  absent from the delivered file) and should get its own audit + spec.
- `tests/test_reddit_collector.py` (4 tests) and
  `tests/test_sentiment_analyzer.py` (6 tests) currently fail in a clean
  environment — unrelated to this session's two fixes; not investigated
  further here to avoid scope creep on this delivery.

---

## Pre-delivery checklist (MANDATORY — human process step)

Per `03_regression_protection.md` Acceptance Criteria: this is a hard
requirement before zipping/delivering this project for review, not an
optional courtesy.

1. [ ] `MINIO_MOCK=1 pytest tests/ -q` run against the archive **about to
       be delivered** — not an older local copy.
2. [ ] Confirm `tests/test_regression_protection.py` specifically shows
       `PASSED` for every test (this is the fast, targeted check — full
       suite in step 1 is the complete one).
3. [ ] If ANY test fails that isn't already listed under "Known issues"
       above for a specific dated reason, STOP. Do not ship. Either fix
       it or add a dated, explained entry here first.
4. [ ] Every new confirmed fix this session gets its own dated entry in
       this file, following the format above (File / Symptom / Root
       cause / Fix / Protected by / Verified).
5. [ ] `git status` / archive diff reviewed once more against this file's
       "Fixed" list — confirm every line item is actually present in the
       files being shipped (catches the exact silent-revert failure mode
       one more time, right before the archive is zipped).

## Manual test command (until real CI is wired to a hosted repo)

```bash
pip install -r requirements.txt
MINIO_MOCK=1 pytest tests/ -q
```

`MINIO_MOCK=1` avoids requiring a live MinIO server / the `minio` package's
network client at import time (`clients/minio_client.py` auto-selects the
local-filesystem mock in `clients/minio_mock.py` when this is set).