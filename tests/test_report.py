"""調査レポート（export/report.py）の不変条件を固定する。

設計の約束：確定が0件のときは数字の根拠がないので、Claude を呼ばず
『確定された棟がありません』を返す（API を1回も叩かない）。ここを pytest で保証する。
"""
from __future__ import annotations

import duckdb

from api.schema import ensure_tables
from export import report
from ingest.pipeline import schema_sql

GEOM = ("POLYGON((139.690 35.680,139.691 35.680,139.691 35.681,"
        "139.690 35.681,139.690 35.680))")


def _setup_no_confirmations(db_path: str) -> None:
    """buildings と candidates だけ入れ、confirmations は空のままにする。"""
    con = duckdb.connect(db_path)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(schema_sql("buildings"))
    ensure_tables(con)
    for bid in ("B1", "B2"):
        con.execute(
            "INSERT INTO buildings (building_id, version, city_code, raw_usage, "
            "raw_storeys_above, is_target, geom_4326) "
            "VALUES (?, '2025', '13104', '461', '9999', TRUE, ST_GeomFromText(?))",
            [bid, GEOM],
        )
    con.execute(
        "INSERT INTO candidates (building_id, version, attribute, value, confidence, "
        "source, reasoning, unresolvable) "
        "VALUES ('B1', '2025', 'usage', '412', 0.5, 'neighbor', 'r', FALSE)")
    con.close()


def test_empty_confirmations_skips_claude(tmp_path, monkeypatch):
    db = str(tmp_path / "t.duckdb")
    _setup_no_confirmations(db)
    monkeypatch.setattr(report, "LOG_PATH", str(tmp_path / "report_log.jsonl"))

    res = report.run(db_path=db, out_dir=str(tmp_path))

    # Claude を呼ばず、確定0件として返す（API 0 回）
    assert res["skipped"] is True
    assert res["api_calls"] == 0
    assert res["confirmed_buildings"] == 0

    md = (tmp_path / "report_13104.md").read_text(encoding="utf-8")
    assert "確定された棟がありません" in md
    assert "0 棟" in md
    # 文章要約（Claude）を謳う行は出さない＝数字の創作が無いことの裏返し
    assert "による要約" not in md


def test_empty_report_mentions_onsite_aggregate(tmp_path, monkeypatch):
    """確定0件でも、確定に依存しない『現地確認が必要な棟』の実数は参考に載る。"""
    db = str(tmp_path / "t.duckdb")
    _setup_no_confirmations(db)
    monkeypatch.setattr(report, "LOG_PATH", str(tmp_path / "report_log.jsonl"))

    report.run(db_path=db, out_dir=str(tmp_path))
    md = (tmp_path / "report_13104.md").read_text(encoding="utf-8")
    assert "現地確認が必要な棟（参考）" in md
