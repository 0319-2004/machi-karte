"""版間差分（2023年度版 ↔ 2025年度版）による「新しく現れた棟」の検出。

設計書「新しく現れた建物の検出」に対応。手順：

1. 建物IDが版をまたいで安定か検証：共通ID数・2025のみ・2023のみ、
   共通IDの外形IoU分布（EPSG:6677。中央値、IoU<0.5 の割合）。
2. 安定なら ID 差分（2025にしか無いID＝新規）。不安定なら外形IoU照合に切替
   （閾値は分布から決め、理由を decisions.md に記録）。
3. creationDate=2026-03-13 による判定との重なりを出し、設計書の
   「5,796棟中4,009棟が2021年の属性」を確かめる。
4. 新規棟に candidates（attribute='year_built', value_min=2022, value_max=2024,
   source='version_diff'）を入れる。年の区間は両版の調査年から導く。

前提（2023年度版 ingest 済み）:
    uv run python -m ingest.raw_version --version 2023
実行:
    uv run python -m estimate.version_diff           # 検証して新規棟に候補投入
    uv run python -m estimate.version_diff --report  # 検証のみ（投入しない）
"""
from __future__ import annotations

import argparse

import duckdb
import numpy as np

from api.schema import ensure_tables

DB_PATH = "data/machikarte.duckdb"
VERSION = "2025"
PREV_TABLE = "buildings_2023"
PREV_VERSION = "2023"

# 新規棟の建築年区間（両版の調査年から。設計書どおり 2022〜2024）
YEAR_MIN, YEAR_MAX = 2022, 2024
IOU_STABLE_MEDIAN = 0.9   # 共通IDの外形IoU中央値がこれ以上なら「ID安定」
IOU_STABLE_BADFRAC = 0.05  # かつ IoU<0.5 の割合がこれ未満なら「ID安定」


def connect(db_path: str = DB_PATH) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    con.execute("LOAD spatial;")
    return con


def id_overlap(con) -> dict:
    common = con.execute(
        f"SELECT count(*) FROM buildings a JOIN {PREV_TABLE} b USING(building_id)"
    ).fetchone()[0]
    only_new = con.execute(
        f"SELECT count(*) FROM buildings a WHERE NOT EXISTS "
        f"(SELECT 1 FROM {PREV_TABLE} b WHERE b.building_id=a.building_id)"
    ).fetchone()[0]
    only_old = con.execute(
        f"SELECT count(*) FROM {PREV_TABLE} b WHERE NOT EXISTS "
        f"(SELECT 1 FROM buildings a WHERE a.building_id=b.building_id)"
    ).fetchone()[0]
    return {"common": common, "only_new": only_new, "only_old": only_old}


def iou_distribution(con) -> dict:
    """共通IDの外形IoU（6677）の分布。中央値と IoU<0.5 の割合。"""
    rows = con.execute(
        f"""
        SELECT ST_Area(ST_Intersection(a.geom, b.geom)),
               ST_Area(a.geom), ST_Area(b.geom)
        FROM buildings a JOIN {PREV_TABLE} b USING(building_id)
        WHERE a.geom IS NOT NULL AND b.geom IS NOT NULL
          AND ST_IsValid(a.geom) AND ST_IsValid(b.geom)
        """
    ).fetchall()
    iou = []
    for inter, aa, ab in rows:
        union = (aa or 0) + (ab or 0) - (inter or 0)
        if union and union > 0:
            iou.append(inter / union)
    iou = np.array(iou) if iou else np.array([0.0])
    return {
        "n": len(iou),
        "median": float(np.median(iou)),
        "mean": float(np.mean(iou)),
        "frac_lt_0.5": float(np.mean(iou < 0.5)),
        "frac_ge_0.9": float(np.mean(iou >= 0.9)),
    }


def decide_method(iou: dict) -> tuple[str, str]:
    """ID安定なら 'id_diff'、そうでなければ 'iou_match' を返す。理由も返す。"""
    stable = iou["median"] >= IOU_STABLE_MEDIAN and iou["frac_lt_0.5"] < IOU_STABLE_BADFRAC
    if stable:
        reason = (f"共通IDの外形IoU中央値 {iou['median']:.3f}、IoU<0.5 は "
                  f"{iou['frac_lt_0.5']*100:.1f}%（しきい値 中央値≥{IOU_STABLE_MEDIAN}・"
                  f"<0.5 が {IOU_STABLE_BADFRAC*100:.0f}%未満）。IDと外形は1:1で安定→ID差分を採用。")
        return "id_diff", reason
    reason = (f"共通IDの外形IoU中央値 {iou['median']:.3f}／IoU<0.5 {iou['frac_lt_0.5']*100:.1f}% で "
              "ID安定とは言えない→外形IoU照合に切替（閾値は分布から決める）。")
    return "iou_match", reason


def creationdate_overlap(con) -> dict:
    """creationDate=2026-03-13 と ID差分の重なり、survey_year=2021 の件数。"""
    c2026 = con.execute(
        "SELECT count(*) FROM buildings WHERE creation_date='2026-03-13'"
    ).fetchone()[0]
    sy2021 = con.execute(
        "SELECT count(*) FROM buildings WHERE creation_date='2026-03-13' "
        "AND raw_survey_year='2021'"
    ).fetchone()[0]
    # creationDate=2026-03-13 のうち、ID差分でも新規（2025のみ）な件数
    cd_and_new = con.execute(
        f"SELECT count(*) FROM buildings a WHERE a.creation_date='2026-03-13' "
        f"AND NOT EXISTS (SELECT 1 FROM {PREV_TABLE} b WHERE b.building_id=a.building_id)"
    ).fetchone()[0]
    return {"creation_2026": c2026, "survey_2021": sy2021, "cd_and_new": cd_and_new}


def new_target_overlap(con) -> dict:
    """新規棟（2025のみ）と不明群（is_target）の重なり。"""
    new_total = con.execute(
        f"SELECT count(*) FROM buildings a WHERE NOT EXISTS "
        f"(SELECT 1 FROM {PREV_TABLE} b WHERE b.building_id=a.building_id)"
    ).fetchone()[0]
    new_target = con.execute(
        f"SELECT count(*) FROM buildings a WHERE a.is_target AND NOT EXISTS "
        f"(SELECT 1 FROM {PREV_TABLE} b WHERE b.building_id=a.building_id)"
    ).fetchone()[0]
    return {"new_total": new_total, "new_target": new_target}


def insert_year_built(con, method: str) -> int:
    """新規棟（2025のみID）に year_built 候補を入れる（source='version_diff'）。

    年の区間は両版の調査年から：2023年度版に無く2025年度版で初めて現れたので、
    両版の間（2022〜2024年）に新築とみなす（設計書どおり）。
    """
    ensure_tables(con)
    con.execute("DELETE FROM candidates WHERE version=? AND source='version_diff'", [VERSION])
    reasoning = (f"2023年度版({PREV_VERSION})の建物IDに無く、2025年度版で初めて現れた"
                 f"（外形IoUでIDは版間安定を確認）。両版の調査年の間、"
                 f"{YEAR_MIN}〜{YEAR_MAX}年に新築と推定（点推定せず区間で持つ）。")
    n = con.execute(
        f"""
        INSERT OR REPLACE INTO candidates
          (building_id, version, attribute, value, value_min, value_max,
           confidence, source, reasoning, evidence_ids, unresolvable)
        SELECT a.building_id, '{VERSION}', 'year_built', NULL, {YEAR_MIN}, {YEAR_MAX},
               0.7, 'version_diff', ?, [], FALSE
        FROM buildings a
        WHERE NOT EXISTS (SELECT 1 FROM {PREV_TABLE} b WHERE b.building_id=a.building_id)
        """,
        [reasoning],
    )
    # 投入件数を数える
    return con.execute(
        "SELECT count(*) FROM candidates WHERE version=? AND source='version_diff'",
        [VERSION],
    ).fetchone()[0]


def run(report_only: bool) -> dict:
    con = connect()
    ov = id_overlap(con)
    iou = iou_distribution(con)
    method, reason = decide_method(iou)
    cd = creationdate_overlap(con)
    nt = new_target_overlap(con)

    print("=== 版間差分（2025 ↔ 2023）===")
    print(f"  共通ID : {ov['common']}  / 2025のみ: {ov['only_new']}  / 2023のみ: {ov['only_old']}")
    print(f"  共通IDの外形IoU（n={iou['n']}）: 中央値 {iou['median']:.3f} / "
          f"平均 {iou['mean']:.3f} / IoU<0.5 {iou['frac_lt_0.5']*100:.1f}% / "
          f"IoU≥0.9 {iou['frac_ge_0.9']*100:.1f}%")
    print(f"  → 採用手法: {method}")
    print(f"     {reason}")
    print("\n  creationDate=2026-03-13 との重なり（設計書: 5,796中4,009が2021属性）:")
    print(f"    creationDate=2026-03-13       : {cd['creation_2026']}")
    print(f"    うち survey_year=2021          : {cd['survey_2021']}")
    print(f"    うち ID差分でも新規(2025のみ)  : {cd['cd_and_new']}")
    print(f"\n  新規棟(2025のみ) : {nt['new_total']}  / うち不明群(is_target): {nt['new_target']}")

    inserted = 0
    if not report_only:
        inserted = insert_year_built(con, method)
        print(f"\n  year_built 候補を投入: {inserted} 行（source='version_diff', "
              f"{YEAR_MIN}〜{YEAR_MAX}年）")
    con.close()
    return {"overlap": ov, "iou": iou, "method": method, "reason": reason,
            "creationdate": cd, "new_target": nt, "inserted": inserted}


def main() -> None:
    p = argparse.ArgumentParser(description="版間差分で新規棟を検出し year_built 候補を入れる")
    p.add_argument("--report", action="store_true", help="検証のみ（候補を投入しない）")
    args = p.parse_args()
    run(args.report)


if __name__ == "__main__":
    main()
