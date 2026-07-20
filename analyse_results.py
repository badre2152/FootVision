"""
analyse_results.py — تحليل نقدي كامل للنتائج بعد sofifa integration
تشغيل: python analyse_results.py
"""
import pandas as pd
import json
import glob
import sys
from pathlib import Path

# ── تحميل البيانات ───────────────────────────────────────────────────────────
def load_latest(pattern):
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"✗ لا يوجد ملف: {pattern}")
        return None
    return files[-1]

scored_file = load_latest("gold/players_scored_*.csv")
top26_file  = load_latest("gold/top26_*.csv")
summary_file= load_latest("gold/squad_summary_*.json")
sofifa_file = load_latest("bronze/sofifa/players_sofifa_*.json")

if not scored_file:
    print("شغّل run_gold.py أولاً")
    sys.exit(1)

df     = pd.read_csv(scored_file)
top26  = pd.read_csv(top26_file)

with open(summary_file) as f:
    summary = json.load(f)

print("=" * 70)
print(f"تحليل النتائج — {scored_file.split(chr(92))[-1]}")
print("=" * 70)

# ── 1. Position breakdown ────────────────────────────────────────────────────
print("\n── Position Breakdown ──")
counts = top26["position"].value_counts().to_dict()
caps   = {"GK": 3, "DEF": 9, "MID": 9, "FWD": 5}
for pos in ["GK", "DEF", "MID", "FWD"]:
    cnt = counts.get(pos, 0)
    ok  = "✅" if cnt == caps[pos] else f"⚠ (cap={caps[pos]})"
    print(f"  {pos}: {cnt} {ok}")

# ── 2. Top 26 مع flags ───────────────────────────────────────────────────────
print("\n── Top-26 Ranked Squad ──")
top_sorted = top26.sort_values("DSSScore", ascending=False)
flags = {
    "Imran Louza":         "⚠ inactive_2526",
    "Mohamed Rabie Hrimat":"⚠ check_data",
    "Samir El Mourabet":   "⚠ Ligue 2",
    "El Mehdi Al Harrar":  "⚠ Botola league bias",
}
for i, (_, r) in enumerate(top_sorted.iterrows(), 1):
    flag = flags.get(r["player_name"], "")
    mental_src = r.get("mental_source", "?")
    mental_tag = f"[{mental_src}]" if mental_src != "statsbomb" else ""
    print(f"  #{r['Rank']:<4} {r['player_name']:<28} {r['position']:<5} "
          f"DSS={r['DSSScore']:.4f}  {mental_tag} {flag}")

# ── 3. MentalScore sources ────────────────────────────────────────────────────
print("\n── MentalScore Sources ──")
if "mental_source" in df.columns:
    src_counts = df["mental_source"].value_counts()
    for src, cnt in src_counts.items():
        bar = "█" * int(cnt / 49 * 20) + "░" * (20 - int(cnt / 49 * 20))
        print(f"  {src:<15} {bar} {cnt}/49")
else:
    print("  mental_source column not found — silver_pipeline.py قد لا يحفظه")

# ── 4. DSS range ─────────────────────────────────────────────────────────────
print("\n── DSS Range Analysis ──")
top_scores = top26["DSSScore"]
print(f"  #1  (max):  {top_scores.max():.4f}")
print(f"  #13 (mid):  {top_scores.sort_values(ascending=False).iloc[12]:.4f}")
print(f"  #26 (min):  {top_scores.min():.4f}")
print(f"  Range:      {top_scores.max()-top_scores.min():.4f}")
print(f"  الفرق #1-#26 vs الجلسة السابقة (0.2727):")
diff = top_scores.max() - top_scores.min()
print(f"    {'تحسّن ✅' if diff > 0.28 else 'لم يتغير بشكل ملحوظ'} (range={diff:.4f})")

# ── 5. intl_caps coverage ────────────────────────────────────────────────────
print("\n── intl_caps Coverage ──")
if "intl_caps" in df.columns:
    nonzero = (df["intl_caps"] > 0).sum()
    zeros   = (df["intl_caps"] == 0).sum()
    print(f"  non-zero caps: {nonzero}/49")
    print(f"  zero (imputed or real): {zeros}/49")
    if "intl_caps_data_missing" in df.columns:
        missing = df["intl_caps_data_missing"].sum()
        print(f"  data_missing flag: {missing} players")

# ── 6. مقارنة مع التشكيلة الرسمية ───────────────────────────────────────────
print("\n── مقارنة مع التشكيلة الرسمية WC2026 ──")
official = {
    "Yassine Bounou","Ahmed Reda Tagnaouti","Munir El Kajoui",
    "Achraf Hakimi","Noussair Mazraoui","Nayef Aguerd","Zakaria El Ouahdi",
    "Issa Diop","Anass Salah-Eddine","Chadi Riad","Redouane Halhal",
    "Sofyan Amrabat","Azzedine Ounahi","Bilal El Khannouss","Ismael Saibari",
    "Neil El Aynaoui","Oussama Targhalline",
    "Youssef En-Nesyri","Soufiane Rahimi","Ayoub El Kaabi",
    "Brahim Diaz","Abde Ezzalzouli","Ayoube Amaimouni-Echghouyab",
}
our_squad = set(top26["player_name"])
in_both   = our_squad & official
our_only  = our_squad - official
off_only  = official  - our_squad

print(f"  في كلا القائمتين: {len(in_both)}/26 ({len(in_both)/26*100:.0f}%)")
print(f"  في DSS فقط:       {len(our_only)}")
for p in sorted(our_only):
    dss = top26[top26.player_name==p]["DSSScore"].values
    dss_str = f"DSS={dss[0]:.4f}" if len(dss) else ""
    print(f"    + {p} {dss_str}")
print(f"  في الرسمية فقط:   {len(off_only)}")
for p in sorted(off_only):
    print(f"    ✗ {p}")

# ── 7. مشاكل حرجة ──────────────────────────────────────────────────────────
print("\n── Issues ──")
issues = []

hrimat = df[df.player_name.str.contains("Hrimat", na=False)]
if not hrimat.empty:
    rank = hrimat.iloc[0]["Rank"]
    dss  = hrimat.iloc[0]["DSSScore"]
    if rank <= 15:
        issues.append(f"🔴 Hrimat #{rank} DSS={dss:.4f} — Botola player في top-15 (يستحق فحص)")

louza = df[df.player_name.str.contains("Louza", na=False)]
if not louza.empty:
    rank = louza.iloc[0]["Rank"]
    mflag= louza.iloc[0].get("minutes_flag","?")
    dss  = louza.iloc[0]["DSSScore"]
    if rank <= 20:
        issues.append(f"⚠ Louza #{rank} DSS={dss:.4f} minutes_flag={mflag} — ACL injury")
    else:
        issues.append(f"✅ Louza #{rank} — minutes_filter يعمل صح")

mourabet = df[df.player_name.str.contains("Mourabet", na=False)]
if not mourabet.empty and mourabet.iloc[0]["player_name"] in our_squad:
    dss = mourabet.iloc[0]["DSSScore"]
    issues.append(f"⚠ Samir El Mourabet في top-26 DSS={dss:.4f} — Ligue 2 player")

# sofifa coverage
if sofifa_file:
    with open(sofifa_file) as f:
        sf = json.load(f)
    sf_covered = sum(1 for p in sf["players"] if "sofifa_composure" in p)
    issues.append(f"ℹ SoFIFA coverage: {sf_covered}/{len(sf['players'])} composure")

for issue in issues:
    print(f"  {issue}")

print("\n" + "=" * 70)
print("التوصية: راجع الـ issues أعلاه وقرر الخطوة التالية")
print("=" * 70)
