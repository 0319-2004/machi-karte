"""Phase 10 評価レポート生成。`make eval` で docs/results.md を再生成する。

数字の出し方は2通り。どちらも「推測の数字は入れない」（CLAUDE.md）:

- 再計算：件数・削減率・階数精度・用途ベースライン・現地確認棟数・処理時間は、
  その場で DB と既存の eval 関数から計算する（件数は毎回データから数える）。
- 記録値：Claude の用途検証（判断不能率・断定誤 0 件・近傍(c)との差）と各費用は、
  課金を伴う一度きりの実行で得た値を docs/decisions.md から引く。表に出典（日付・
  再現コマンド）を併記する。全棟推定の費用は claude_infer_log.jsonl があれば集計する。

実行：  uv run python -m eval.results      （または make eval）
"""
from __future__ import annotations

import json
import os

from api.onsite import aggregate_unresolved  # noqa: F401 (出典として併記)
from eval.baselines import run_bands
from eval.holdout import DATA_DIR, VERSION, connect
from estimate.storeys import evaluate_storeys
from ingest.pipeline import summarize

OUT = "docs/results.md"
DERIVED_DIR = os.path.join(DATA_DIR, "derived")
BANDS = ["0-10m", "10-20m", "20-45m", "45m+"]

# --- 記録値（docs/decisions.md 由来。課金を伴う一度きりの実行なので make eval では再計算しない） ---
# すべて実測。該当の日付・見出し・再現コマンドを出典として表に併記する。
REC_CLAUDE_EVAL = {
    "source": "docs/decisions.md 2026-11-04「Claude 用途・階数の既知棟検証（30棟×2モデル, 5.5世代）」",
    "reproduce": "make claude-eval CLAUDE_EVAL_N=30 MODELS=claude-opus-5-5,claude-haiku-5-5",
    "note": ("高さ20m以上の holdout 既知棟から30棟。近傍分布・ベースライン・階高は "
             "train のみ＋自棟 exclude でリーク防止。"),
    # 列: モデル / 用途正解(全体) / 用途正解(判断分) / 判断不能率 / 断定して外した件数 /
    #     近傍(c)との差(20m超) / 階数 一致/±1 / 費用
    "rows": [
        ["claude-opus-5-5", "10.0%", "60.0%（5棟）", "83.3%", "0", "−23.3pt", "30.0% / 60.0%",
         "$0.2262（29棟 / 17,472 tok）"],
        ["claude-haiku-5-5", "13.3%", "57.1%（7棟）", "76.7%", "0", "−20.0pt", "30.0% / 60.0%",
         "$0.0118（30棟 / 29,376 tok）"],
    ],
    "total_cost": "$0.254（smoke test 1棟込みの実費用合計）",
    "conclusion": ("用途は2モデルとも高さ20m以上で近傍(c)を超えなかった → 用途は近傍の候補提示の"
                   "ままにする。**根拠なく断定して外した件数は両モデルとも 0 件**（conf≥0.6 の誤答ゼロ）。"
                   "判断不能を安定して返しており、設計書「Claude が判断不能を返さない」停止条件には該当しない。"),
}
REC_COSTS = [  # 一度きりの実行で記録された費用（出典つき）
    ["レポート生成（export/report.py）", "$0.02896（1,567 tok, claude-opus-5-5）",
     "decisions.md 2026-11-05 / `uv run python -m export.report`（log: data/derived/report_log.jsonl）"],
    ["現地要約（api/onsite.py, 区分ごと1回）", "$0.0074（API 1回, claude-opus-5-5）",
     "decisions.md 2026-11-04 / `make onsite`"],
    ["用途・階数の既知棟検証（eval）", "$0.254（30棟×2モデル＋smoke）",
     "decisions.md 2026-11-04 / `make claude-eval ...`"],
]


def _table_exists(con, name: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name=?", [name]).fetchone()[0])


def _pct(x) -> str:
    return "—" if x is None else f"{x:.1f}%"


def processing_time(con) -> dict:
    """1棟あたり処理時間（机上 desk のみ）。/api/metrics と同じ集計。

    現地（onsite）は opened_at≒confirmed_at で0秒寄りになり中央値を歪めるため除く。
    そのまま確定（accepted_candidate 全True）と修正して確定を分ける。
    """
    import statistics
    rows = con.execute(
        """
        SELECT building_id,
               (epoch_ms(max(confirmed_at)) - epoch_ms(min(opened_at))) / 1000.0 AS sec,
               bool_and(accepted_candidate) AS all_accepted
        FROM confirmations WHERE version=? AND source='desk'
        GROUP BY building_id
        """, [VERSION]).fetchall()
    secs = [max(0.0, r[1]) for r in rows]
    keep = [max(0.0, r[1]) for r in rows if r[2]]
    edit = [max(0.0, r[1]) for r in rows if not r[2]]
    med = lambda xs: round(statistics.median(xs), 1) if xs else None
    return {"processed": len(rows), "median_sec": med(secs),
            "keep_n": len(keep), "keep_med": med(keep),
            "edit_n": len(edit), "edit_med": med(edit)}


def claude_infer_cost() -> dict | None:
    """全棟推定の費用。claude_infer_log.jsonl があれば集計、無ければ None（未実施）。"""
    p = os.path.join(DERIVED_DIR, "claude_infer_log.jsonl")
    if not os.path.exists(p):
        return None
    rows = [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()]
    if not rows:
        return None
    ids = {r.get("building_id") for r in rows}
    total = sum(r.get("cost_usd", 0.0) for r in rows)
    return {"buildings": len(ids), "total": total,
            "per_building": total / len(ids) if ids else None}


def unresolved_count(con) -> int:
    """現地確認が必要な棟（判断不能 unresolvable=TRUE の棟・重複なし）。"""
    return con.execute(
        "SELECT count(DISTINCT building_id) FROM candidates WHERE version=? AND unresolvable",
        [VERSION]).fetchone()[0]


def build_markdown(s, es, rb, pt, infer_cost, n_unres) -> str:
    fh = es["methods"]["flat3.8"]          # 高さ÷一律階高（decisions.md の見出し値）
    exact, w1 = fh["band_exact"], fh["band_w1"]
    n = fh["band_n"]
    ba, tc = rb["band_acc"], rb["tgt_counts"]
    L = []
    w = L.append
    w("# まちカルテ 評価レポート（Phase 10）\n")
    w("このファイルは `make eval` で再生成する。**数字は推測を入れず、その場で再計算するか "
      "`docs/decisions.md` の実測記録から引く**。各節に出典（スクリプト／再現コマンド）を併記する。\n")
    w(f"対象：version={VERSION}、DB=`data/machikarte.duckdb`。\n")

    # 1. 削減
    w("## 1. 訪問が必要な棟の削減\n")
    w(f"全棟 **{s['total']:,}** 棟 → 不明群（調査が及んでいない棟）**{s['unknown']:,}** 棟。"
      f"**削減率 {s['reduction']:.1f}%**。\n")
    w(f"- 設計書値との照合：{'全項目一致' if s['all_ok'] else '不一致あり（要確認）'}"
      f"（全棟/不明群/高さ帯）。\n")
    w("- 出典：`ingest/pipeline.py` summarize()（`make ingest`）・`api/main.py` stats()。"
      "本レポートで DB から再計算。\n")

    # 2. 階数
    w("## 2. 階数の精度（高さ ÷ 階高〔一律 3.8m・設計書再現〕、holdout n={:,}）\n".format(es["n"]))
    w("| 高さ帯 | 完全一致 | ±1階 | n |\n| --- | --- | --- | --- |\n"
      + "".join(f"| {b} | {_pct(exact[b])} | {_pct(w1[b])} | {n[b]:,} |\n" for b in BANDS)
      + f"| 全体 | {_pct(fh['overall'][0])} | {_pct(fh['overall'][1])} | {es['n']:,} |\n"
      + f"| **不明群の高さ分布で重み付け** | **{_pct(fh['w_exact'])}** | **{_pct(fh['w_w1'])}** | — |\n")
    w(f"- 用途別の階高を学習して当てると全体はわずかに上振れ"
      f"（完全一致 {_pct(es['methods']['用途別・真']['overall'][0])} / "
      f"±1階 {_pct(es['methods']['用途別・真']['overall'][1])}）。上表は設計書再現の一律 3.8m。\n")
    w("- 20m 以上で完全一致が落ちるのは構造的（高層ほど小さな階高誤差が丸めで効く／低層部の"
      "吹き抜け等で measuredHeight がずれる）。だから 20m 超は候補扱いにしている。\n")
    w("- 出典：`estimate/storeys.py` evaluate_storeys()（`make eval` の `estimate.storeys --eval`）。再計算。\n")

    # 3. 用途ベースライン
    w("## 3. 用途のベースライン（holdout n={:,}）\n".format(es["n"]))
    w("| 高さ帯 | (a) 最頻値 | (b) 近傍 | (c) 近傍＋高さ±3m | 不明群(帯別) |\n| --- | --- | --- | --- | --- |\n"
      + "".join(f"| {b} | {_pct(ba['a'][b])} | {_pct(ba['b'][b])} | {_pct(ba['c'][b])} | {tc[b]:,} |\n"
               for b in BANDS)
      + f"| 全体 | {_pct(ba['a']['_all'])} | {_pct(ba['b']['_all'])} | {_pct(ba['c']['_all'])} | — |\n"
      + f"| **不明群重み** | **{_pct(rb['weighted']['a'])}** | **{_pct(rb['weighted']['b'])}** "
        f"| **{_pct(rb['weighted']['c'])}** | — |\n")
    w("- **POI 結合は用途推定の主軸にならない**（合成は全帯で近傍を下回る。decisions.md 2026-11-03 "
      "「POI 結合は用途推定の主軸にならない」/ `uv run python -m eval.poi_eval`）。\n")
    w("- **Claude も近傍(c)を超えなかった**（§4、記録値）。→ 用途は近傍の候補提示にとどめる。\n")
    w("- 出典：`eval/baselines.py` run_bands()（`make eval` の `eval.baselines`）。再計算。\n")

    # 4. Claude 用途・階数検証（記録値）
    w("## 4. Claude の用途・階数検証（記録値：課金のため再計算しない）\n")
    w("| モデル | 用途 正解(全体) | 用途 正解(判断分) | 判断不能率 | 断定して外した | 近傍(c)との差(20m超) | 階数 一致/±1 | 費用 |\n")
    w("| --- | --- | --- | --- | --- | --- | --- | --- |\n")
    for r in REC_CLAUDE_EVAL["rows"]:
        w("| " + " | ".join(r) + " |\n")
    w(f"\n- {REC_CLAUDE_EVAL['conclusion']}\n")
    w(f"- 合計費用：{REC_CLAUDE_EVAL['total_cost']}。\n")
    w(f"- {REC_CLAUDE_EVAL['note']}\n")
    w(f"- 出典：{REC_CLAUDE_EVAL['source']} / 再現 `{REC_CLAUDE_EVAL['reproduce']}`。\n")

    # 5. 処理時間
    w("## 5. 1棟あたりの処理時間（机上トリアージ desk のみ）\n")
    if pt["processed"]:
        secf = lambda v: "—" if v is None else f"{v} 秒"
        w(f"- 中央値 **{secf(pt['median_sec'])}/棟**（処理 {pt['processed']} 棟）。\n")
        w(f"- そのまま確定：中央値 {secf(pt['keep_med'])}（{pt['keep_n']} 棟）／"
          f"修正して確定：中央値 {secf(pt['edit_med'])}（{pt['edit_n']} 棟）。\n")
        w(f"- 注：確定 {pt['processed']} 棟の小標本（デモの机上分）。現地（onsite）は "
          "opened_at≒confirmed_at で歪むため除外。確定が溜まるほど安定する。\n")
    else:
        w("- **記録なし**（確定した机上棟が無い）。確定が溜まると自動で埋まる。\n")
    w("- 出典：`confirmations`（opened_at/confirmed_at/accepted_candidate）を `/api/metrics` と"
      "同じ式で集計（decisions.md 2026-11-05「計測は source='desk' に限定」）。再計算。\n")

    # 6. 費用
    w("## 6. Claude API の費用\n")
    if infer_cost:
        w(f"- **全棟推定：総額 ${infer_cost['total']:.4f}／1棟あたり ${infer_cost['per_building']:.4f}"
          f"（{infer_cost['buildings']:,} 棟）**。出典：`data/derived/claude_infer_log.jsonl`（`make claude-infer`）。\n")
    else:
        w("- **全棟推定：未実施**（`data/derived/claude_infer_log.jsonl` が無い）。"
          "実行後にここへ総額・1棟あたり費用が入る（`make claude-infer`）。\n")
    for name, val, src in REC_COSTS:
        w(f"- {name}：{val}。出典：{src}。\n")
    w("- 注：キャッシュ読みの乗数は opus/sonnet 5.5 で実際 0.05× だが compute_cost は 0.1× 固定で"
      "僅かに過大評価（decisions.md 2026-11-04）。\n")

    # 7. 現地確認
    w("## 7. 現地確認が必要な棟\n")
    w(f"**{n_unres:,} 棟**（すべて高さ欠損で階数が推定不可＝判断不能）。推測で埋めず、現地で外観から"
      "階を数える／フロア案内・EV 表示で最上階を確認／難しければ区役所の建築計画概要書で補う。\n")
    w("- 出典：`candidates`（unresolvable=TRUE の棟数）・`api/onsite.py` aggregate_unresolved()"
      "（`make onsite`）。再計算。\n")
    return "".join(L)


def main() -> None:
    con = connect()
    if not _table_exists(con, "eval_split"):
        con.close()
        from eval.holdout import run as holdout_run
        holdout_run()                      # eval_split を作る（seed 固定の20%分割）
        con = connect()
    s = summarize(con, VERSION)
    es = evaluate_storeys(con, VERSION)
    rb = run_bands(con=con)
    pt = processing_time(con)
    n_unres = unresolved_count(con)
    con.close()
    md = build_markdown(s, es, rb, pt, claude_infer_cost(), n_unres)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"  → {OUT} を生成（削減 {s['total']:,}→{s['unknown']:,}={s['reduction']:.1f}%, "
          f"階数 不明群重み {es['methods']['flat3.8']['w_exact']:.1f}%, 現地確認 {n_unres} 棟）")


if __name__ == "__main__":
    main()
