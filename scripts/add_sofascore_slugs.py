import argparse
from pathlib import Path

import pandas as pd

CONFIRMED_SLUG_MAP: dict[int, str] = {
    1140885: "mehdi-harrar",
    970630:  "el-mehdi-benabid",
    360938:  "bono",
    141967:  "munir-mohamedi",
    359280:  "ahmed-reda-tagnaouti",
    1432574: "yanis-benchaouch",
    814594:  "achraf-hakimi",
    847030:  "noussair-mazraoui",
    877102:  "nayef-aguerd",
    1064218: "chadi-riad",
    1134470: "zakaria-el-ouahdi",
    825719:  "issa-diop",
    961684:  "anass-salah-eddine",
    919484:  "youssef-belammari",
    1143667: "redouane-halhal",
    1101232: "abdel-abqar",
    1597145: "abdelhamid-ait-boudlal",
    1064026: "omar-el-hilali",
    919642:  "chibi-mohamed",
    1518450: "marwan-louadni",
    847714:  "marwane-saadane",
    1440668: "ali-maamar",
    359272:  "sofyan-amrabat",
    991421:  "azzedine-ounahi",
    1126569: "bilal-el-khannouss",
    1063767: "ismael-saibari",
    1128530: "neil-el-aynaoui",
    1564180: "bouaddi-ayyoub",
    1605921: "el-mourabet-samir",
    919489:  "mohamed-rabie-hrimat",
    965943:  "imran-louza",
    1095731: "oussama-targhalline",
    1129527: "bilal-nadir",
    944164:  "sofiane-diop",
    835485:  "brahim-diaz",
    953995:  "soufiane-rahimi",
    919793:  "ayoub-el-kaabi",
    1011375: "abdessamad-ezzalzouli",
    1142675: "chemsdine-talbi",
    1638338: "yassine-gessime",
    1898148: "ayoube-amaimouni-echghouyab",
    1392829: "amine-sbai",
    991478:  "amine-adli",
    1515917: "maamma-othmane",
    1606013: "yassir-zabiri",
    1089108: "ilias-akhomach",
    1103080: "tawfik-bentayeb",
    846470:  "youssef-en-nesyri",
    1403090: "rayane-bounida",
}


def add_slugs(csv_path: Path) -> None:
    df = pd.read_csv(csv_path)

    if "sofascore_id" not in df.columns:
        raise ValueError(f"'sofascore_id' column not found in {csv_path}")

    def _lookup(sid):
        if pd.isna(sid):
            return None
        try:
            return CONFIRMED_SLUG_MAP.get(int(sid))
        except (ValueError, TypeError):
            return None

    df["sofascore_slug"] = df["sofascore_id"].map(_lookup)

    cols = list(df.columns)
    cols.remove("sofascore_slug")
    insert_at = cols.index("sofascore_id") + 1
    cols.insert(insert_at, "sofascore_slug")
    df = df[cols]

    missing = df[df["sofascore_slug"].isna()]
    if not missing.empty:
        print(f"WARNING: {len(missing)} player(s) have no confirmed slug:")
        for _, row in missing.iterrows():
            print(f"  {row['player_id']}  sofascore_id={row.get('sofascore_id')}")
    else:
        print(f"All {len(df)} players have a confirmed sofascore_slug ✅")

    df.to_csv(csv_path, index=False)
    print(f"Written: {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add confirmed sofascore_slug to players_49.csv")
    parser.add_argument(
        "--csv",
        default="seeds/players_49.csv",
        help="Path to players_49.csv (default: seeds/players_49.csv)",
    )
    args = parser.parse_args()
    add_slugs(Path(args.csv))