"""センチネル値の正規化。

実データ（2025年度版 全31ファイル・13104）で確認したセンチネル：
- usage = '461'（不明）
- detailed_usage = '0'（不明）
- storeys_above = 9999
- survey_year = '0001'
- height / roof_edge_area = -9999
なお '1011' は `uro:fireproofStructureType`（耐火構造不明）の値であり、
ここで保持する列のセンチネルではない（検証で特定済み）。

素の値は raw_* 列にそのまま残し、ここでは地図・集計で使う正規化列を作る。
"""
from __future__ import annotations

USAGE_SENTINEL = "461"
DETAILED_USAGE_SENTINEL = "0"
STOREYS_SENTINEL = "9999"
SURVEY_YEAR_SENTINEL = "0001"
NUMERIC_SENTINEL = "-9999"


def norm_str(raw: str | None, sentinel: str) -> str | None:
    return None if raw is None or raw == sentinel else raw


def norm_int(raw: str | None, sentinel: str) -> int | None:
    if raw is None or raw == sentinel:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def norm_float(raw: str | None, sentinel: str) -> float | None:
    if raw is None or raw == sentinel:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return None if v == float(sentinel) else v


def normalize(row):
    """BuildingRow から正規化後の値の dict を返す。"""
    return {
        "usage": norm_str(row.raw_usage, USAGE_SENTINEL),
        "detailed_usage": norm_str(row.raw_detailed_usage, DETAILED_USAGE_SENTINEL),
        "storeys_above": norm_int(row.raw_storeys_above, STOREYS_SENTINEL),
        "survey_year": norm_str(row.raw_survey_year, SURVEY_YEAR_SENTINEL),
        "height_m": norm_float(row.raw_height, NUMERIC_SENTINEL),
        "roof_edge_area": norm_float(row.raw_roof_edge_area, NUMERIC_SENTINEL),
    }
