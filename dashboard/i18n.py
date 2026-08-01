# dashboard/i18n.py
# Football Atlas — Dashboard Localization (Arabic / French / English)
#
# Added for issue #15 (Arabic / French Dashboard Localization).
#
# Scope decision (confirmed with project owner): full bilingual/trilingual
# toggle, not a single fixed-language build. A sidebar language switcher
# lets the user pick English / Français / العربية at any time; the choice
# is stored in st.session_state["lang"] so it persists across page
# navigation (Streamlit reruns the script on every interaction, but
# session_state survives within one browser tab — same pattern already
# used for the sidebar filters in sidebar.py).
#
# Currently fully wired into:
#   - sidebar.py                (branding, filters, language switcher itself)
#   - app.py                    (Executive Dashboard — nav/title + full page)
#   - pages/4_final_squad.py    (highest-value page — full translation)
#   - pages/5_selection_audit.py(full translation, incl. Plotly axis labels)
# and wired for *navigation titles only* (st.title / page_config) on the
# remaining pages, per the minimum scope in 15_localization.md. Expanding
# full in-page translation to the remaining pages is a follow-up (see
# implementation plan step 4 in the spec) — just add more keys below and
# swap the hardcoded strings for t(...) calls the same way.
#
# Usage:
#   from dashboard.i18n import t, render_language_switcher, get_lang
#   render_language_switcher()          # call once in sidebar.py
#   st.title(t("nav.final_squad_title"))
#   st.caption(t("final_squad.dna_caption"))
#   st.warning(t("final_squad.mismatch_warning", mismatches="; ".join(mismatches)))

import streamlit as st

LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "ar": "العربية",
}

DEFAULT_LANG = "en"

# RTL languages need special CSS handling (see apply_rtl_css below) —
# Streamlit has no built-in RTL support, this is the one language in our
# set that needs it.
RTL_LANGUAGES = {"ar"}


STRINGS = {
    # ────────────────────────────────────────────────────────────────
    # Sidebar (sidebar.py) — shown on every page
    # ────────────────────────────────────────────────────────────────
    "sidebar.brand_title": {
        "en": "Football Atlas",
        "fr": "Football Atlas",
        "ar": "أطلس الكرة",
    },
    "sidebar.brand_subtitle": {
        "en": "Morocco World Cup 2026 — Decision Support System",
        "fr": "Coupe du Monde 2026 Maroc — Système d'aide à la décision",
        "ar": "كأس العالم 2026 المغرب — نظام دعم القرار",
    },
    "sidebar.language_label": {
        "en": "🌐 Language",
        "fr": "🌐 Langue",
        "ar": "🌐 اللغة",
    },
    "sidebar.filters_header": {
        "en": "🔍 Filters",
        "fr": "🔍 Filtres",
        "ar": "🔍 عوامل التصفية",
    },
    "sidebar.position_label": {
        "en": "Position",
        "fr": "Poste",
        "ar": "المركز",
    },
    "sidebar.search_label": {
        "en": "Search player name",
        "fr": "Rechercher un joueur",
        "ar": "البحث عن اسم اللاعب",
    },
    "sidebar.search_placeholder": {
        "en": "e.g. Hakimi",
        "fr": "ex. Hakimi",
        "ar": "مثال: حكيمي",
    },
    "sidebar.reset_button": {
        "en": "Reset filters",
        "fr": "Réinitialiser les filtres",
        "ar": "إعادة ضبط عوامل التصفية",
    },
    "sidebar.footer": {
        "en": (
            "Built for the Football Atlas graduation project. "
            "Data: FBref, Understat, SofaScore, StatsBomb Open Data, "
            "Transfermarkt, FlashScore, Fotmob, SoFIFA, Wikidata."
        ),
        "fr": (
            "Réalisé pour le projet de fin d'études Football Atlas. "
            "Données : FBref, Understat, SofaScore, StatsBomb Open Data, "
            "Transfermarkt, FlashScore, Fotmob, SoFIFA, Wikidata."
        ),
        "ar": (
            "أُنجز في إطار مشروع التخرج Football Atlas. "
            "مصادر البيانات: FBref وUnderstat وSofaScore وStatsBomb Open Data و"
            "Transfermarkt وFlashScore وFotmob وSoFIFA وWikidata."
        ),
    },

    # ────────────────────────────────────────────────────────────────
    # Navigation / page titles — minimum required scope (all pages)
    # ────────────────────────────────────────────────────────────────
    "nav.app_title": {
        "en": "⚽ Football Atlas — Executive Dashboard",
        "fr": "⚽ Football Atlas — Tableau de bord exécutif",
        "ar": "⚽ أطلس الكرة — لوحة القيادة التنفيذية",
    },
    "nav.player_analysis_title": {
        "en": "📊 Player Analysis",
        "fr": "📊 Analyse des joueurs",
        "ar": "📊 تحليل اللاعبين",
    },
    "nav.chemistry_title": {
        "en": "🔗 Chemistry Matrix",
        "fr": "🔗 Matrice de complémentarité",
        "ar": "🔗 مصفوفة الانسجام",
    },
    "nav.final_squad_title": {
        "en": "🏆 Final Squad — 26 Players",
        "fr": "🏆 Effectif final — 26 joueurs",
        "ar": "🏆 القائمة النهائية — 26 لاعبًا",
    },
    "nav.selection_audit_title": {
        "en": "🔍 Selection Audit — DSSScore vs. the Real 2026 World Cup Squad",
        "fr": "🔍 Audit de sélection — DSSScore vs. la véritable liste Coupe du Monde 2026",
        "ar": "🔍 تدقيق الاختيار — مقارنة DSSScore بالقائمة الحقيقية لكأس العالم 2026",
    },
    "nav.fitness_title": {
        "en": "🩺 Fitness & Rotation",
        "fr": "🩺 Forme physique & rotation",
        "ar": "🩺 اللياقة والتناوب",
    },
    "nav.tactical_board_title": {
        "en": "🧭 Tactical Board",
        "fr": "🧭 Tableau tactique",
        "ar": "🧭 اللوحة التكتيكية",
    },
    "nav.sentiment_title": {
        "en": "💬 Public Sentiment",
        "fr": "💬 Opinion publique",
        "ar": "💬 الرأي العام",
    },
    "nav.competition_title": {
        "en": "🌍 Competition Context",
        "fr": "🌍 Contexte de la compétition",
        "ar": "🌍 سياق المنافسة",
    },
    "nav.setpiece_title": {
        "en": "🎯 Set Piece & Clutch",
        "fr": "🎯 Coups de pied arrêtés & décisif",
        "ar": "🎯 الكرات الثابتة واللحظات الحاسمة",
    },
    "nav.squad_builder_title": {
        "en": "🛠️ Squad Builder",
        "fr": "🛠️ Constructeur d'effectif",
        "ar": "🛠️ باني التشكيلة",
    },

    # ────────────────────────────────────────────────────────────────
    # app.py — Executive Dashboard (full translation)
    # ────────────────────────────────────────────────────────────────
    "app.caption": {
        "en": "Decision Support System — Morocco World Cup 2026 squad selection",
        "fr": "Système d'aide à la décision — sélection de l'effectif Coupe du Monde 2026 Maroc",
        "ar": "نظام دعم القرار — اختيار قائمة المغرب لكأس العالم 2026",
    },
    "app.source_caption": {
        "en": "Data source: `{source}` — generated {generated_at}",
        "fr": "Source des données : `{source}` — générée le {generated_at}",
        "ar": "مصدر البيانات: `{source}` — تم الإنشاء بتاريخ {generated_at}",
    },
    "app.no_gold_error": {
        "en": (
            "No Gold output found in `gold/`. Run the pipeline first:\n\n"
            "```bash\npython run_bronze.py\npython run_silver.py\npython run_gold.py --top-n 26\n```"
        ),
        "fr": (
            "Aucune sortie Gold trouvée dans `gold/`. Exécutez d'abord le pipeline :\n\n"
            "```bash\npython run_bronze.py\npython run_silver.py\npython run_gold.py --top-n 26\n```"
        ),
        "ar": (
            "لا توجد مخرجات Gold في `gold/`. شغّل خط المعالجة أولًا:\n\n"
            "```bash\npython run_bronze.py\npython run_silver.py\npython run_gold.py --top-n 26\n```"
        ),
    },
    "app.kpi_candidates": {
        "en": "Candidates tracked",
        "fr": "Candidats suivis",
        "ar": "المرشحون المتابَعون",
    },
    "app.kpi_squad_size": {
        "en": "Squad size",
        "fr": "Taille de l'effectif",
        "ar": "حجم القائمة",
    },
    "app.kpi_top_player": {
        "en": "Top player",
        "fr": "Meilleur joueur",
        "ar": "أفضل لاعب",
    },
    "app.kpi_dss_range": {
        "en": "DSS range (all 49)",
        "fr": "Plage DSS (les 49 joueurs)",
        "ar": "نطاق DSS (كل الـ 49 لاعبًا)",
    },
    "app.position_breakdown": {
        "en": "Position breakdown",
        "fr": "Répartition par poste",
        "ar": "التوزيع حسب المركز",
    },
    "app.position_mismatch_warning": {
        "en": (
            "Position counts don't match the standard 3/9/9/5 cap — "
            "if this squad_summary.json predates 2026-07-14, it may have "
            "the position-cap bug (see AUDIT_AND_FIXES / MASTER_SPEC "
            "addendum): {mismatches}"
        ),
        "fr": (
            "Les effectifs par poste ne correspondent pas au format standard 3/9/9/5 — "
            "si ce squad_summary.json est antérieur au 2026-07-14, il peut contenir "
            "le bug de plafond par poste (voir l'annexe AUDIT_AND_FIXES / MASTER_SPEC) : "
            "{mismatches}"
        ),
        "ar": (
            "أعداد اللاعبين حسب المركز لا تطابق السقف المعتمد 3/9/9/5 — "
            "إذا كان ملف squad_summary.json هذا سابقًا لتاريخ 2026-07-14، فقد يحتوي "
            "على خلل سقف المراكز (راجع ملحق AUDIT_AND_FIXES / MASTER_SPEC): {mismatches}"
        ),
    },
    "app.flags_header": {
        "en": "⚠️ Flags",
        "fr": "⚠️ Alertes",
        "ar": "⚠️ تنبيهات",
    },
    "app.flag_injury": {
        "en": "High injury risk ({n}):",
        "fr": "Risque de blessure élevé ({n}) :",
        "ar": "خطر إصابة مرتفع ({n}):",
    },
    "app.flag_limited_data": {
        "en": "Limited data coverage ({n}):",
        "fr": "Couverture de données limitée ({n}) :",
        "ar": "تغطية بيانات محدودة ({n}):",
    },
    "app.flag_intl_caps": {
        "en": "Intl caps possibly missing ({n}):",
        "fr": "Sélections internationales possiblement manquantes ({n}) :",
        "ar": "احتمال نقص بيانات المشاركات الدولية ({n}):",
    },
    "app.no_flags": {
        "en": "No flags raised for this squad.",
        "fr": "Aucune alerte pour cet effectif.",
        "ar": "لا توجد تنبيهات لهذه القائمة.",
    },
    "app.score_spread_header": {
        "en": "Score spread (display scaling)",
        "fr": "Étalement des scores (mise à l'échelle d'affichage)",
        "ar": "تباين النتائج (تحجيم العرض)",
    },
    "app.score_spread_text": {
        "en": (
            "Elite-to-fringe gap: **{gap_raw}** (raw DSS) "
            "→ **{gap_pct}** (display-scaled, ×{factor}). "
            "See `DSSScore_pct` in the table below — ranking is identical, only the visual spread is stretched."
        ),
        "fr": (
            "Écart entre l'élite et les marges : **{gap_raw}** (DSS brut) "
            "→ **{gap_pct}** (mis à l'échelle pour l'affichage, ×{factor}). "
            "Voir `DSSScore_pct` dans le tableau ci-dessous — le classement est identique, seul l'étalement visuel est étiré."
        ),
        "ar": (
            "الفارق بين النخبة والهامش: **{gap_raw}** (DSS الخام) "
            "→ **{gap_pct}** (بعد التحجيم للعرض، ×{factor}). "
            "انظر `DSSScore_pct` في الجدول أدناه — الترتيب لم يتغير، فقط التباين البصري تم توسيعه."
        ),
    },
    "app.squad_table_header": {
        "en": "Squad — top {n}",
        "fr": "Effectif — top {n}",
        "ar": "القائمة — أفضل {n}",
    },
    "app.no_match_filters": {
        "en": "No players match the current sidebar filters.",
        "fr": "Aucun joueur ne correspond aux filtres actuels.",
        "ar": "لا يوجد لاعبون مطابقون لعوامل التصفية الحالية.",
    },
    "app.missing_cols_caption": {
        "en": (
            "ℹ️ This run predates the 2026-07-14 session — {cols} "
            "not present. Re-run `run_gold.py` with the updated code to see them."
        ),
        "fr": (
            "ℹ️ Cette exécution est antérieure à la session du 2026-07-14 — {cols} "
            "absent(s). Relancez `run_gold.py` avec le code mis à jour pour les voir."
        ),
        "ar": (
            "ℹ️ هذه النتيجة سابقة لتحديث 2026-07-14 — {cols} "
            "غير متوفرة. أعد تشغيل `run_gold.py` بالنسخة المحدّثة لعرضها."
        ),
    },
    "app.footer_hint": {
        "en": "👈 Use the sidebar to open **Player Analysis**, **Chemistry Matrix**, and **Final Squad** pages.",
        "fr": "👈 Utilisez la barre latérale pour ouvrir **Analyse des joueurs**, **Matrice de complémentarité** et **Effectif final**.",
        "ar": "👈 استخدم الشريط الجانبي لفتح صفحات **تحليل اللاعبين** و**مصفوفة الانسجام** و**القائمة النهائية**.",
    },

    # ────────────────────────────────────────────────────────────────
    # pages/4_final_squad.py (full translation — decision-critical page)
    # ────────────────────────────────────────────────────────────────
    "final_squad.no_gold_error": {
        "en": "No Gold output found. Run `run_gold.py` first.",
        "fr": "Aucune sortie Gold trouvée. Exécutez d'abord `run_gold.py`.",
        "ar": "لا توجد مخرجات Gold. شغّل `run_gold.py` أولًا.",
    },
    "final_squad.no_match_filters": {
        "en": "No players match the current sidebar filters — adjust them and try again.",
        "fr": "Aucun joueur ne correspond aux filtres actuels — ajustez-les et réessayez.",
        "ar": "لا يوجد لاعبون مطابقون لعوامل التصفية الحالية — عدّلها وحاول مجددًا.",
    },
    "final_squad.dna_caption": {
        "en": (
            "🏷️ Player DNA badges are a descriptive play-style tag from K-Means "
            "clustering — NOT a factor in DSSScore or squad selection."
        ),
        "fr": (
            "🏷️ Les badges « ADN joueur » sont une étiquette descriptive de style de jeu "
            "issue d'un regroupement K-Means — ce N'EST PAS un facteur du DSSScore ni de la sélection."
        ),
        "ar": (
            "🏷️ شارات «الهوية الفنية للاعب» هي وسم وصفي لأسلوب اللعب ناتج عن تصنيف K-Means — "
            "وهي لا تدخل ضمن عوامل DSSScore ولا في اختيار القائمة."
        ),
    },
    "final_squad.justification_default": {
        "en": "DSSScore {score}",
        "fr": "DSSScore {score}",
        "ar": "DSSScore {score}",
    },
    "final_squad.justification": {
        "en": "Strongest in **{label}** ({score})",
        "fr": "Point fort : **{label}** ({score})",
        "ar": "أقوى نقطة: **{label}** ({score})",
    },
    "final_squad.label_performance": {
        "en": "on-pitch output", "fr": "rendement sur le terrain", "ar": "الأداء الميداني",
    },
    "final_squad.label_form": {
        "en": "recent form", "fr": "forme récente", "ar": "الفورمة الحالية",
    },
    "final_squad.label_mental": {
        "en": "mental profile", "fr": "profil mental", "ar": "الجانب النفسي",
    },
    "final_squad.label_availability": {
        "en": "fitness/availability", "fr": "forme physique/disponibilité", "ar": "اللياقة/التوفر",
    },
    "final_squad.label_international": {
        "en": "international experience", "fr": "expérience internationale", "ar": "الخبرة الدولية",
    },
    "final_squad.label_chemistry": {
        "en": "squad chemistry", "fr": "complémentarité collective", "ar": "الانسجام الجماعي",
    },
    "final_squad.label_pressure": {
        "en": "big-match performance", "fr": "performance dans les grands matchs", "ar": "الأداء في المباريات الكبرى",
    },
    "final_squad.label_flexibility": {
        "en": "positional versatility", "fr": "polyvalence positionnelle", "ar": "المرونة في المراكز",
    },
    "final_squad.position_gk": {
        "en": "🧤 Goalkeepers", "fr": "🧤 Gardiens", "ar": "🧤 حراس المرمى",
    },
    "final_squad.position_def": {
        "en": "🛡️ Defenders", "fr": "🛡️ Défenseurs", "ar": "🛡️ المدافعون",
    },
    "final_squad.position_mid": {
        "en": "⚙️ Midfielders", "fr": "⚙️ Milieux", "ar": "⚙️ لاعبو الوسط",
    },
    "final_squad.position_fwd": {
        "en": "⚔️ Forwards", "fr": "⚔️ Attaquants", "ar": "⚔️ المهاجمون",
    },
    "final_squad.dss_progress": {
        "en": "DSS {score}",
        "fr": "DSS {score}",
        "ar": "DSS {score}",
    },
    "final_squad.mismatch_warning": {
        "en": "Position composition doesn't match the standard 3/9/9/5 cap: {mismatches}",
        "fr": "La composition par poste ne correspond pas au format standard 3/9/9/5 : {mismatches}",
        "ar": "توزيع المراكز لا يطابق السقف المعتمد 3/9/9/5: {mismatches}",
    },
    "final_squad.mismatch_success": {
        "en": "Squad composition matches the standard cap: 3 GK / 9 DEF / 9 MID / 5 FWD.",
        "fr": "La composition de l'effectif respecte le format standard : 3 GB / 9 DEF / 9 MIL / 5 ATT.",
        "ar": "تركيبة القائمة تطابق السقف المعتمد: 3 حراس / 9 مدافعين / 9 وسط / 5 مهاجمين.",
    },
    # #20 — Coach/Staff Notes compact view
    "final_squad.note_expander": {
        "en": "📝 Coach note",
        "fr": "📝 Note du staff",
        "ar": "📝 ملاحظة الطاقم الفني",
    },
    "final_squad.note_empty": {
        "en": "No coach note yet for this player.",
        "fr": "Aucune note du staff pour ce joueur pour l'instant.",
        "ar": "لا توجد ملاحظة بعد لهذا اللاعب.",
    },
    "final_squad.note_last_edited": {
        "en": "Last edited by {author} at {time} UTC",
        "fr": "Dernière modification par {author} le {time} UTC",
        "ar": "آخر تعديل بواسطة {author} في {time} UTC",
    },

    # #16 — Squad Age/Experience Balance
    "final_squad.age_balance_header": {
        "en": "📈 Age & Experience Balance",
        "fr": "📈 Équilibre âge & expérience",
        "ar": "📈 توازن العمر والخبرة",
    },
    "final_squad.age_balance_caption": {
        "en": "Age computed as of {as_of} (squad decision date), not today's date.",
        "fr": "Âge calculé à la date du {as_of} (date de décision de l'effectif), pas à la date du jour.",
        "ar": "العمر محسوب بتاريخ {as_of} (تاريخ قرار القائمة)، وليس بتاريخ اليوم.",
    },
    "final_squad.age_balance_no_source": {
        "en": "⚠️ Age/experience data (`wiki_dob`, `intl_caps`) unavailable — run `run_gold.py` to refresh `players_scored`.",
        "fr": "⚠️ Données d'âge/expérience (`wiki_dob`, `intl_caps`) indisponibles — exécutez `run_gold.py` pour actualiser `players_scored`.",
        "ar": "⚠️ بيانات العمر/الخبرة (`wiki_dob`, `intl_caps`) غير متوفرة — شغّل `run_gold.py` لتحديث `players_scored`.",
    },
    "final_squad.tier_young": {"en": "Young (<23)", "fr": "Jeune (<23)", "ar": "شاب (أقل من 23)"},
    "final_squad.tier_prime": {"en": "Prime (23-29)", "fr": "Prime (23-29)", "ar": "الذروة (23-29)"},
    "final_squad.tier_experienced": {"en": "Experienced (30+)", "fr": "Expérimenté (30+)", "ar": "خبرة عالية (30+)"},
    "final_squad.tier_unknown": {
        "en": "Unknown (missing DOB)",
        "fr": "Inconnu (date de naissance manquante)",
        "ar": "غير معروف (تاريخ ميلاد ناقص)",
    },
    "final_squad.age_balance_unknown_warning": {
        "en": "⚠️ {n} player(s) have a missing/unparseable date of birth — shown as \"Unknown\" below rather than dropped: {names}",
        "fr": "⚠️ {n} joueur(s) ont une date de naissance manquante/illisible — affiché(s) comme « Inconnu » ci-dessous plutôt qu'exclu(s) : {names}",
        "ar": "⚠️ {n} لاعب/لاعبين لديهم تاريخ ميلاد ناقص/غير قابل للقراءة — يظهرون كـ«غير معروف» أدناه بدلًا من استبعادهم: {names}",
    },
    "final_squad.age_balance_position_summary": {
        "en": "**{pos}:** {young} young / {prime} prime / {experienced} experienced{unknown_suffix}",
        "fr": "**{pos} :** {young} jeune(s) / {prime} prime / {experienced} expérimenté(s){unknown_suffix}",
        "ar": "**{pos}:** {young} شاب / {prime} ذروة / {experienced} خبرة{unknown_suffix}",
    },
    "final_squad.age_balance_position_summary_unknown_suffix": {
        "en": " / {n} unknown",
        "fr": " / {n} inconnu(s)",
        "ar": " / {n} غير معروف",
    },
    "final_squad.age_balance_chart_title": {
        "en": "Age tier distribution by position",
        "fr": "Répartition des tranches d'âge par poste",
        "ar": "توزيع الفئات العمرية حسب المركز",
    },
    "final_squad.age_balance_chart_yaxis": {
        "en": "Players", "fr": "Joueurs", "ar": "اللاعبون",
    },
    "final_squad.age_balance_chart_xaxis": {
        "en": "Position", "fr": "Poste", "ar": "المركز",
    },
    "final_squad.age_balance_chart_legend": {
        "en": "Age tier", "fr": "Tranche d'âge", "ar": "الفئة العمرية",
    },
    "final_squad.age_balance_avg_caps_header": {
        "en": "Average international caps by age tier (experience proxy)",
        "fr": "Sélections internationales moyennes par tranche d'âge (indicateur d'expérience)",
        "ar": "متوسط المشاركات الدولية حسب الفئة العمرية (مؤشر الخبرة)",
    },

    # ────────────────────────────────────────────────────────────────
    # pages/5_selection_audit.py (full translation)
    # ────────────────────────────────────────────────────────────────
    "audit.no_data_warning": {
        "en": (
            "No `gold/selection_audit_*.json` found. Run:\n\n"
            "```\npython run_selection_audit.py\n```\n\n"
            "This is a standalone analysis script, deliberately not part of "
            "`run_gold.py` — see `run_selection_audit.py`'s header comment for why."
        ),
        "fr": (
            "Aucun fichier `gold/selection_audit_*.json` trouvé. Exécutez :\n\n"
            "```\npython run_selection_audit.py\n```\n\n"
            "Il s'agit d'un script d'analyse autonome, volontairement séparé de "
            "`run_gold.py` — voir le commentaire d'en-tête de `run_selection_audit.py` pour comprendre pourquoi."
        ),
        "ar": (
            "لم يتم العثور على `gold/selection_audit_*.json`. شغّل:\n\n"
            "```\npython run_selection_audit.py\n```\n\n"
            "هذا سكريبت تحليل مستقل، وهو عمدًا غير مدمج ضمن "
            "`run_gold.py` — راجع تعليق الترويسة في `run_selection_audit.py` لمعرفة السبب."
        ),
    },
    "audit.source_caption": {
        "en": "Ground truth: {real_source}  ·  Source Gold run: `{source_file}`  ·  Report generated: {generated_at}",
        "fr": "Référence réelle : {real_source}  ·  Exécution Gold source : `{source_file}`  ·  Rapport généré : {generated_at}",
        "ar": "المرجع الحقيقي: {real_source}  ·  تشغيل Gold المصدر: `{source_file}`  ·  تاريخ إنشاء التقرير: {generated_at}",
    },
    "audit.unmatched_error": {
        "en": (
            "⚠️ {n} real-squad player(s) had NO confident name match in the candidate pool "
            "— this audit is comparing against an INCOMPLETE ground truth: {names}"
        ),
        "fr": (
            "⚠️ {n} joueur(s) de l'effectif réel n'ont trouvé AUCUNE correspondance fiable "
            "dans le pool de candidats — cet audit compare avec une référence INCOMPLÈTE : {names}"
        ),
        "ar": (
            "⚠️ {n} لاعب/لاعبين من القائمة الحقيقية لم يُعثر لهم على تطابق موثوق في مجموعة المرشحين "
            "— هذا التدقيق يقارن مع مرجع غير مكتمل: {names}"
        ),
    },
    "audit.headline_header": {
        "en": "Headline result",
        "fr": "Résultat principal",
        "ar": "النتيجة الرئيسية",
    },
    "audit.overlap_metric": {
        "en": "Overlap", "fr": "Recouvrement", "ar": "التطابق",
    },
    "audit.pvalue_metric": {
        "en": "P(this much overlap | random selection)",
        "fr": "P(ce niveau de recouvrement | sélection aléatoire)",
        "ar": "احتمال (هذا القدر من التطابق | اختيار عشوائي)",
    },
    "audit.verdict_metric": {
        "en": "Verdict", "fr": "Verdict", "ar": "الحكم",
    },
    "audit.verdict_beats": {
        "en": "Beats random", "fr": "Meilleur que l'aléatoire", "ar": "أفضل من العشوائي",
    },
    "audit.verdict_random": {
        "en": "≈ Random", "fr": "≈ Aléatoire", "ar": "≈ عشوائي",
    },
    "audit.verdict_error": {
        "en": (
            "🔴 **The DSSScore-based top-26 selection is NOT statistically "
            "distinguishable from a random draw of 26 out of 49** (p={pvalue}, "
            "needs < 0.05 to claim real skill). This is a single real-world validation "
            "point (n=1 coach decision), heavily confounded by a mid-cycle coaching "
            "change (Regragui → Ouahbi, 2026-03-05) the scoring formula was never tuned "
            "against. It does not *prove* the formula is wrong, but it is the only real "
            "evidence this project has, and it is not supportive."
        ),
        "fr": (
            "🔴 **La sélection du top-26 basée sur le DSSScore N'EST PAS statistiquement "
            "distinguable d'un tirage aléatoire de 26 joueurs parmi 49** (p={pvalue}, "
            "il faut < 0,05 pour revendiquer une réelle compétence). Il s'agit d'un unique "
            "point de validation réel (n=1 décision d'entraîneur), fortement confondu par un "
            "changement d'entraîneur en cours de cycle (Regragui → Ouahbi, 2026-03-05) contre "
            "lequel la formule de score n'a jamais été calibrée. Cela ne *prouve* pas que la "
            "formule est fausse, mais c'est la seule preuve réelle dont dispose ce projet, et "
            "elle n'est pas favorable."
        ),
        "ar": (
            "🔴 **اختيار أفضل 26 لاعبًا استنادًا إلى DSSScore لا يمكن تمييزه إحصائيًا "
            "عن سحب عشوائي لـ26 لاعبًا من أصل 49** (p={pvalue}، يجب أن تكون القيمة "
            "أقل من 0.05 لادعاء وجود مهارة حقيقية). هذه نقطة تحقق واحدة من الواقع "
            "(n=1 قرار مدرب)، وهي متأثرة بشدة بتغيير المدرب في منتصف الدورة "
            "(الركراكي ← وحبي، 2026-03-05) الذي لم تُعايَر صيغة التقييم على أساسه أبدًا. "
            "هذا لا *يثبت* أن الصيغة خاطئة، لكنه الدليل الحقيقي الوحيد المتوفر لهذا المشروع، "
            "وهو غير داعم لها."
        ),
    },
    "audit.verdict_success": {
        "en": "🟢 Beats random selection at p<0.05 (p={pvalue}).",
        "fr": "🟢 Meilleur qu'une sélection aléatoire avec p<0,05 (p={pvalue}).",
        "ar": "🟢 أفضل من الاختيار العشوائي عند p<0.05 (p={pvalue}).",
    },
    "audit.disagreement_header": {
        "en": "Where the formula and reality disagree",
        "fr": "Où la formule et la réalité divergent",
        "ar": "أين تختلف الصيغة عن الواقع",
    },
    "audit.dss_favored_header": {
        "en": "DSSScore favored (top-{n}), coach did NOT pick",
        "fr": "Favorisé par DSSScore (top-{n}), NON retenu par le sélectionneur",
        "ar": "فضّله DSSScore (ضمن أفضل {n})، لكن المدرب لم يختره",
    },
    "audit.dss_favored_empty": {
        "en": "None — every DSSScore top pick was actually selected.",
        "fr": "Aucun — tous les meilleurs choix du DSSScore ont réellement été sélectionnés.",
        "ar": "لا يوجد — تم اختيار جميع مرشحي DSSScore الأوائل فعليًا.",
    },
    "audit.coach_picked_header": {
        "en": "Coach picked, DSSScore ranked outside top-{n}",
        "fr": "Retenu par le sélectionneur, classé par DSSScore hors du top-{n}",
        "ar": "اختاره المدرب، لكن ترتيبه في DSSScore خارج أفضل {n}",
    },
    "audit.coach_picked_empty": {
        "en": "None — every real pick was already in the DSSScore top selection.",
        "fr": "Aucun — tous les choix réels figuraient déjà dans la sélection DSSScore.",
        "ar": "لا يوجد — جميع الاختيارات الحقيقية كانت أصلًا ضمن اختيار DSSScore الأول.",
    },
    "audit.loocv_header": {
        "en": "Exploratory: does a data-driven reweighting of the same 9 sub-scores do better?",
        "fr": "Exploratoire : une repondération des 9 sous-scores basée sur les données fait-elle mieux ?",
        "ar": "استكشافي: هل إعادة ترجيح النتائج الفرعية التسع بالاعتماد على البيانات يعطي نتيجة أفضل؟",
    },
    "audit.loocv_caption": {
        "en": (
            "⚠️ LOOCV Logistic Regression on the 9 Gold sub-scores (not DSSScore itself, "
            "to avoid circularity). This is exploratory/interpretability output, NOT a "
            "predictor to trust for a future squad — see "
            "`analytics/ml/selection_classifier.py` module docstring."
        ),
        "fr": (
            "⚠️ Régression logistique LOOCV sur les 9 sous-scores Gold (pas le DSSScore "
            "lui-même, pour éviter toute circularité). Il s'agit d'un résultat exploratoire/"
            "d'interprétation, PAS d'un prédicteur fiable pour un futur effectif — voir "
            "le docstring du module `analytics/ml/selection_classifier.py`."
        ),
        "ar": (
            "⚠️ انحدار لوجستي بطريقة LOOCV على النتائج الفرعية التسع لـGold (وليس DSSScore "
            "نفسه، تجنبًا للدائرية). هذه مخرجات استكشافية/تفسيرية فقط، وليست نموذجًا "
            "يمكن الوثوق به للتنبؤ بقائمة مستقبلية — راجع توثيق وحدة "
            "`analytics/ml/selection_classifier.py`."
        ),
    },
    "audit.metric_accuracy": {"en": "Accuracy", "fr": "Précision globale", "ar": "الدقة"},
    "audit.metric_precision": {"en": "Precision", "fr": "Précision", "ar": "الدقة الموجبة"},
    "audit.metric_recall": {"en": "Recall", "fr": "Rappel", "ar": "الاستدعاء"},
    "audit.metric_f1": {"en": "F1", "fr": "F1", "ar": "F1"},
    "audit.loocv_verdict_beats": {
        "en": "Beats random baseline (p<0.05)",
        "fr": "Meilleur que la ligne de base aléatoire (p<0,05)",
        "ar": "أفضل من الأساس العشوائي (p<0.05)",
    },
    "audit.loocv_verdict_not": {
        "en": "Does NOT beat random baseline",
        "fr": "Ne bat PAS la ligne de base aléatoire",
        "ar": "لا يتفوق على الأساس العشوائي",
    },
    "audit.loocv_verdict_text": {
        "en": "**{verdict}** — p={pvalue} (n={n})",
        "fr": "**{verdict}** — p={pvalue} (n={n})",
        "ar": "**{verdict}** — p={pvalue} (n={n})",
    },
    "audit.chart_title": {
        "en": "Standardized logistic regression coefficients (interpretation only)",
        "fr": "Coefficients standardisés de la régression logistique (interprétation uniquement)",
        "ar": "معاملات الانحدار اللوجستي المعيارية (للتفسير فقط)",
    },
    "audit.chart_xaxis": {
        "en": "Coefficient (standardized)",
        "fr": "Coefficient (standardisé)",
        "ar": "المعامل (معياري)",
    },
    "audit.loocv_footer_caption": {
        "en": (
            "Do not over-read individual signs/magnitudes — n=49 with correlated "
            "features means coefficients can be unstable. Green = pushes toward "
            "selection, red = pushes away, in this one fitted model."
        ),
        "fr": (
            "N'accordez pas trop d'importance aux signes/magnitudes individuels — n=49 avec "
            "des variables corrélées signifie que les coefficients peuvent être instables. "
            "Vert = pousse vers la sélection, rouge = pousse contre, dans ce modèle ajusté unique."
        ),
        "ar": (
            "لا تفرط في تفسير إشارات/قيم المعاملات الفردية — عينة n=49 مع خصائص مترابطة "
            "تعني أن المعاملات قد تكون غير مستقرة. الأخضر = يدفع نحو الاختيار، الأحمر = يدفع "
            "ضده، في هذا النموذج المفرد الذي تم ملاءمته."
        ),
    },
    "audit.injury_header": {
        "en": "Related: Injury Risk Index (transparent, rule-based)",
        "fr": "Connexe : Indice de risque de blessure (transparent, à base de règles)",
        "ar": "ذو صلة: مؤشر خطر الإصابة (شفاف وقائم على قواعد)",
    },
    "audit.injury_caption": {
        "en": (
            "⚠️ NOT a trained model, NOT combined into DSSScore (AvailabilityScore "
            "already accounts for injury history there — see "
            "`analytics/ml/injury_predictor.py` module docstring for why this stays "
            "separate). A fixed-weight composite of age, injury history, and injury "
            "type, for medical/coaching staff review. Run `python run_injury_predictor.py` "
            "to refresh."
        ),
        "fr": (
            "⚠️ Ce N'EST PAS un modèle entraîné, il N'EST PAS intégré au DSSScore (l'AvailabilityScore "
            "prend déjà en compte l'historique des blessures — voir le docstring du module "
            "`analytics/ml/injury_predictor.py` pour comprendre pourquoi il reste séparé). "
            "Un composite à poids fixes de l'âge, de l'historique des blessures et du type de "
            "blessure, destiné à la revue du staff médical/technique. Exécutez "
            "`python run_injury_predictor.py` pour l'actualiser."
        ),
        "ar": (
            "⚠️ هذا ليس نموذجًا مدرّبًا، وغير مدمج في DSSScore (AvailabilityScore يأخذ بالفعل "
            "بعين الاعتبار تاريخ الإصابات — راجع توثيق وحدة `analytics/ml/injury_predictor.py` "
            "لمعرفة سبب بقائه منفصلاً). هو مؤشر مركّب بأوزان ثابتة يعتمد على العمر وتاريخ "
            "الإصابات ونوعها، مخصص لمراجعة الطاقم الطبي/التدريبي. شغّل "
            "`python run_injury_predictor.py` لتحديثه."
        ),
    },
    "audit.risk_low": {"en": "🟢 Low risk", "fr": "🟢 Risque faible", "ar": "🟢 خطر منخفض"},
    "audit.risk_medium": {"en": "🟡 Medium risk", "fr": "🟡 Risque moyen", "ar": "🟡 خطر متوسط"},
    "audit.risk_high": {"en": "🔴 High risk", "fr": "🔴 Risque élevé", "ar": "🔴 خطر مرتفع"},
    "audit.low_confidence_warning": {
        "en": (
            "⚠️ {n_low}/{n_total} players ({pct}) flagged **low confidence** "
            "(missing/unclear birth date or injury-type data) — "
            "verify manually with medical staff before acting on their level."
        ),
        "fr": (
            "⚠️ {n_low}/{n_total} joueurs ({pct}) marqués **faible confiance** "
            "(date de naissance ou type de blessure manquant/imprécis) — "
            "vérifiez manuellement avec le staff médical avant d'agir sur leur niveau."
        ),
        "ar": (
            "⚠️ {n_low}/{n_total} لاعبين ({pct}) تم تصنيفهم بـ**ثقة منخفضة** "
            "(بيانات تاريخ الميلاد أو نوع الإصابة ناقصة أو غير واضحة) — "
            "تحقق يدويًا مع الطاقم الطبي قبل اتخاذ أي إجراء بناءً على مستواهم."
        ),
    },
    "audit.low_confidence_expander": {
        "en": "Show low-confidence players",
        "fr": "Afficher les joueurs à faible confiance",
        "ar": "عرض اللاعبين ذوي الثقة المنخفضة",
    },
    "audit.highest_risk_header": {
        "en": "Highest risk (top 10)",
        "fr": "Risque le plus élevé (top 10)",
        "ar": "الأعلى خطورة (أفضل 10)",
    },
    "audit.injury_footer_caption": {
        "en": "ℹ️ Run `python run_injury_predictor.py` to also show the injury risk index here.",
        "fr": "ℹ️ Exécutez `python run_injury_predictor.py` pour afficher également l'indice de risque de blessure ici.",
        "ar": "ℹ️ شغّل `python run_injury_predictor.py` لعرض مؤشر خطر الإصابة هنا أيضًا.",
    },
}


def get_lang() -> str:
    """Returns the currently selected language code, defaulting to DEFAULT_LANG."""
    return st.session_state.get("lang", DEFAULT_LANG)


def is_rtl(lang: str | None = None) -> bool:
    return (lang or get_lang()) in RTL_LANGUAGES


def t(key: str, **kwargs) -> str:
    """
    Translates `key` into the currently selected language and formats it
    with any keyword arguments given (using str.format).

    Falls back to English, then to the raw key itself, if a translation is
    missing — this keeps a missing string from crashing the whole page.
    """
    lang = get_lang()
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def render_language_switcher() -> None:
    """
    Renders the sidebar language switcher (EN / FR / AR) and applies RTL
    CSS immediately when Arabic is selected. Call once near the top of
    render_sidebar(), before any other translated text is drawn, so the
    rest of the sidebar (and page) picks up the new language on the same
    run.
    """
    st.session_state.setdefault("lang", DEFAULT_LANG)

    codes = list(LANGUAGES.keys())
    current_index = codes.index(st.session_state["lang"]) if st.session_state["lang"] in codes else 0

    selected = st.selectbox(
        t("sidebar.language_label"),
        options=codes,
        index=current_index,
        format_func=lambda code: LANGUAGES[code],
        key="sidebar_language_select",
    )
    if selected != st.session_state["lang"]:
        st.session_state["lang"] = selected
        st.rerun()

    apply_rtl_css()


def apply_rtl_css() -> None:
    """
    Injects RTL layout CSS when Arabic is active.

    Streamlit has no native RTL mode, so this flips text direction and
    alignment for the elements that carry translated prose (titles,
    markdown, captions, alerts, form labels, tabs) while deliberately
    leaving numeric/tabular/chart surfaces (st.metric values, st.dataframe,
    Plotly charts) in LTR — mixing RTL text with LTR numbers is standard
    practice for Arabic UIs and avoids scrambled-looking figures/axes.

    This has NOT been visually verified in a live browser (no browser
    rendering available in this environment) — spec explicitly calls out
    RTL as "the most likely place for silent visual bugs", so please
    smoke-test `streamlit run dashboard/app.py` with the Arabic option
    selected before treating this as done.
    """
    if not is_rtl():
        return
    st.markdown(
        """
        <style>
        /* Flip text-bearing elements to RTL */
        section.main .block-container,
        section[data-testid="stSidebar"] .block-container,
        .stMarkdown, .stCaption, .stAlert, .stException,
        h1, h2, h3, h4, h5, h6, p, label, li {
            direction: rtl;
            text-align: right;
        }
        /* Keep numbers, tables, and charts LTR so figures/axes don't scramble */
        [data-testid="stMetric"], [data-testid="stMetricValue"],
        [data-testid="stMetricDelta"], [data-testid="stDataFrame"],
        [data-testid="stDataFrameResizable"], .js-plotly-plot,
        [data-testid="stTable"], code, pre {
            direction: ltr;
            text-align: left;
        }
        /* st.metric label should still read RTL even though the value is LTR */
        [data-testid="stMetricLabel"] {
            direction: rtl;
            text-align: right;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )