# まちカルテ（仮）

> 本リポジトリは「Built with Claude: Japan 2026」（開発期間 2026-11-02 12:00 〜 11-08 20:00 JST）の参加作品です。

PLATEAU 新宿区（13104）の建物属性のうち、調査が及んでいない建物（不明群）を自動で絞り込み、
属性を推定し、人が地図で確認・確定して、自治体向けの更新候補リスト（CSV / GeoJSON）を出力する。

骨格：**抽出（自動）→ 推定（Claude）→ 確認・確定（人）→ 更新候補の出力（自動）**

設計の正本は [`docs/design.md`](docs/design.md)。

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

未定（リポジトリのライセンスは ODbL / CC BY-SA の継承義務と整合させて決める。決定後にここへ記載）。

## 帰属表示

使用データの出典・ライセンス表記は [`docs/attribution.md`](docs/attribution.md) にまとめる。
