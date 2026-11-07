"""推定・確定・根拠・計測のテーブル定義（設計書「データモデル」）。

**buildings を壊さない別スクリプト。** ingest（buildings の構築）とは独立に、
candidates / confirmations / evidence と、計測用の open_events を作る。

- candidates   : 推定結果（属性×ソースごとに1行。判断不能も value=NULL で1行）
- confirmations: 人の確定（opened_at / confirmed_at が「1棟あたり数秒」の出どころ）
- evidence     : 推定の根拠の実体（POI・画像・版間差分）。candidates.evidence_ids が参照
- open_events  : 建物を開いた時刻をサーバー側で保持（確定時に opened_at を引く）
- survey_plans : PC で選んだ調査対象の受け渡し（QR で現地スマホへ渡す）。進捗は
                 confirmations との突き合わせで数えるので、ここには状態を持たない

使い方:
    uv run python -m api.schema          # テーブルを作る（既存は壊さない）
    uv run python -m api.schema --reset-confirmations  # 確定履歴だけ消す（デモ撮り直し）
"""
from __future__ import annotations

import argparse
import os

import duckdb

DB_PATH = os.path.join(os.environ.get("DATA_DIR", "./data"), "machikarte.duckdb")

# attribute / source / confidence の値域（設計書のデータモデル）
ATTRIBUTES = ("usage", "storeys", "year_built", "detailed_usage")
SOURCES = ("poi", "osm_building", "image", "neighbor", "height", "version_diff")

_DDL = [
    # 推定結果。1棟×属性×ソースで1行。建築年は区間（value_min/value_max）で持つ。
    # 判断不能は value=NULL, confidence=0, reasoning に理由（行が無いのとは別物）。
    """
    CREATE TABLE IF NOT EXISTS candidates (
        building_id  VARCHAR,
        version      VARCHAR,
        attribute    VARCHAR,     -- usage / storeys / year_built / detailed_usage
        value        VARCHAR,     -- 点推定（NULL=判断不能）
        value_min    INTEGER,     -- 建築年など区間の下限
        value_max    INTEGER,     -- 建築年など区間の上限
        confidence   DOUBLE,      -- 0.0〜1.0（画面には出さず高・中・低に畳む）
        source       VARCHAR,     -- poi/osm_building/image/neighbor/height/version_diff
        reasoning    VARCHAR,     -- 根拠の文章（画面の主役）
        evidence_ids VARCHAR[],   -- evidence.evidence_id の配列
        unresolvable BOOLEAN DEFAULT FALSE,
        created_at   TIMESTAMP DEFAULT now(),
        PRIMARY KEY (building_id, version, attribute, source)
    );
    """,
    # 人の確定。1棟×属性で1行。opened_at/confirmed_at は1棟の操作セッション共通。
    # accepted_candidate=TRUE が「そのまま確定」、FALSE が「修正して確定」。
    # source：机上（既存の確認画面）='desk' / 現地調査（スマホ）='onsite'。区別できるように持つ。
    """
    CREATE TABLE IF NOT EXISTS confirmations (
        building_id       VARCHAR,
        version           VARCHAR,
        attribute         VARCHAR,
        confirmed_value   VARCHAR,
        accepted_candidate BOOLEAN,  -- そのまま=TRUE / 修正=FALSE
        source            VARCHAR DEFAULT 'desk',  -- desk（机上）/ onsite（現地）
        opened_at         TIMESTAMP, -- 計測用（サーバー側で記録）
        confirmed_at      TIMESTAMP, -- 計測用（サーバー側で記録）
        confirmed_by      VARCHAR,
        operator_note     VARCHAR,
        PRIMARY KEY (building_id, version, attribute)
    );
    """,
    # 根拠の実体。POI・画像・版間差分など。detail は JSON 文字列で柔軟に持つ。
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id VARCHAR PRIMARY KEY,
        building_id VARCHAR,
        version     VARCHAR,
        kind        VARCHAR,   -- poi / osm_building / image / version_diff
        label       VARCHAR,   -- 画面に出す短いラベル（POI名・カテゴリ等）
        detail      VARCHAR,   -- JSON（lon/lat, category, distance_m, url など）
        created_at  TIMESTAMP DEFAULT now()
    );
    """,
    # 計測用：建物を開いた時刻をサーバー側で保持（クライアントに依存しない）。
    # 確定時にここから opened_at を引いて confirmations に書く。
    """
    CREATE TABLE IF NOT EXISTS open_events (
        building_id VARCHAR,
        version     VARCHAR,
        opened_at   TIMESTAMP,
        PRIMARY KEY (building_id, version)
    );
    """,
    # 調査計画：PC で選んだ棟のまとまりを保存し、QR で現地スマホへ渡す。
    # building_ids は建物IDの配列。進捗（何棟終わったか）は confirmations との
    # 突き合わせで都度数えるので、ここには done 等の状態を持たない。
    """
    CREATE TABLE IF NOT EXISTS survey_plans (
        plan_id      VARCHAR PRIMARY KEY,
        version      VARCHAR,
        title        VARCHAR,       -- 画面表示用の短い名前（既定は「調査計画（N棟）」）
        building_ids VARCHAR[],     -- 対象の建物IDの配列
        created_at   TIMESTAMP DEFAULT now(),
        created_by   VARCHAR        -- 作成者（任意）
    );
    """,
]


# 既存 DB への追記（冪等）。古い confirmations には source 列が無いので足す。
_MIGRATIONS = [
    "ALTER TABLE confirmations ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'desk'",
]


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    """candidates / confirmations / evidence / open_events / survey_plans を作る（冪等）。"""
    for ddl in _DDL:
        con.execute(ddl)
    for mig in _MIGRATIONS:
        con.execute(mig)


def reset_confirmations(con: duckdb.DuckDBPyConnection) -> None:
    """確定履歴と計測だけを消す（candidates/evidence/buildings は触らない）。"""
    con.execute("DELETE FROM confirmations")
    con.execute("DELETE FROM open_events")


def _connect(db_path: str = DB_PATH) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    con.execute("LOAD spatial;")
    return con


def main() -> None:
    p = argparse.ArgumentParser(description="推定・確定・根拠・計測テーブルを作る")
    p.add_argument("--db", default=DB_PATH)
    p.add_argument("--reset-confirmations", action="store_true",
                   help="確定履歴と計測だけ消す（デモ撮り直し用）")
    args = p.parse_args()

    con = _connect(args.db)
    ensure_tables(con)
    if args.reset_confirmations:
        reset_confirmations(con)
        print("確定履歴と計測（confirmations / open_events）を消去した。")
    else:
        print("テーブルを用意した: candidates / confirmations / evidence / "
              "open_events / survey_plans")
    con.close()


if __name__ == "__main__":
    main()
