# まちカルテ（仮）

> 本リポジトリは「Built with Claude: Japan 2026」（開発期間 2026-11-02 12:00 〜 11-08 20:00 JST）の参加作品です。

PLATEAU 新宿区（13104）の建物属性のうち、調査が及んでいない建物（不明群）を自動で絞り込み、
属性を推定し、人が地図で確認・確定して、自治体向けの更新候補リスト（CSV / GeoJSON）を出力する。

骨格：**抽出（自動）→ 推定（Claude）→ 確認・確定（人）→ 更新候補の出力（自動）**

設計の正本は [`docs/design.md`](docs/design.md)。

## 評価（Phase 10）

数字は実測のみ。**`make eval` で [`docs/results.md`](docs/results.md) に再生成**する（件数・精度は毎回 DB から計算、Claude 検証・費用は課金を伴う一度きりの実測を `docs/decisions.md` から引く。各数字に出典を併記）。主な結果：

- **訪問が必要な棟：57,780 → 4,982（削減率 91.4%）**
- **階数の精度（不明群の高さ分布で重み付け）：完全一致 69.5% / ±1階 95.5%**
- **用途：近傍＋高さのベースライン（不明群重み 62.3%）を POI 結合も Claude も超えず → 用途は候補提示にとどめる**
- **Claude が根拠なく断定して外した件数：0（判断不能を安定して返す）**
- **現地確認が必要：521 棟（高さ欠損で階数が判断不能）**

詳細・最新値は [`docs/results.md`](docs/results.md)。

## セットアップ

Python 3.12 + [uv](https://docs.astral.sh/uv/) を使う。

```bash
uv sync                 # 依存をインストール（.venv を作成）
cp .env.example .env     # APIキー等を記入
```

主なターゲットは `Makefile` を参照（`ingest` / `serve` / `eval` / `export` / `test`）。

## データの取得手順

生データと派生データは `data/` 以下に置く（git 管理外。配置の詳細は [`data/README.md`](data/README.md)）。

- **PLATEAU 新宿区（13104）CityGML** … 2025年度版を `$DATA_DIR/plateau/2025/`、版間差分用に 2023年度版を `$DATA_DIR/plateau/2023/`
- **POI** … Overture Maps Places と OpenStreetMap の新宿区範囲
- **オルソ** … 東京都区部オルソ（東京都デジタルツイン）の新宿区分

取得先・格納パスは `data/README.md` にまとめる。

## ライセンス

- **このリポジトリのコードは [MIT ライセンス](LICENSE)**（Copyright (c) 2026 YNRTR）。
- **`data/` はリポジトリに含めない。** 生データ・派生データ・キャッシュは各データ源のライセンスにそれぞれ従う。
- **出力ファイル（更新候補リスト）には、使ったデータ源のライセンスが及ぶ。** `source` が `poi` / `osm_building` の行は OpenStreetMap 由来を含むため ODbL の対象になり得る。
- **現地調査の徒歩経路は OSRM（BSD-2-Clause）で取得する。** デモは公開サーバー `router.project-osrm.org` を使うが、**本番は自前の OSRM サーバーを立てる前提**（公開サーバーは SLA が無く、負荷・可用性の保証が無いため）。現在地は経路取得のためだけに使い、保存しない。OSRM に送るのは座標のみ。経路データは OpenStreetMap（ODbL）由来。

詳細は [`docs/attribution.md`](docs/attribution.md) の「ライセンス方針」を参照。

## 帰属表示

使用データの出典・ライセンス表記は [`docs/attribution.md`](docs/attribution.md) にまとめる。
