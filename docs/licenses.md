# 依存ライブラリのライセンス一覧

このリポジトリのコードは **MIT ライセンス**（`LICENSE`）。依存はすべて permissive
（MIT / BSD / Apache-2.0）で、コピーレフトは無く、MIT との併用に支障はない。
データ源の出典・ライセンスは [`attribution.md`](attribution.md) を参照。

バージョンは `pyproject.toml` / `uv.lock`（検証時の実インストール値）。

## Python（`pyproject.toml`）

| ライブラリ | ライセンス | 用途 |
| --- | --- | --- |
| anthropic | Apache-2.0 | Claude API（推定・要約・レポート・音声振り分け） |
| duckdb | MIT | 分析用DB（spatial 拡張）・属性の保管 |
| fastapi | MIT | API サーバー |
| uvicorn | BSD-3-Clause | ASGI サーバー |
| starlette | BSD-3-Clause | FastAPI の基盤 |
| pydantic | MIT | スキーマ・バリデーション |
| httpx | BSD-3-Clause | HTTP クライアント |
| lxml | BSD-3-Clause | CityGML（XML）の解析 |
| pyproj | MIT | 座標系の変換（6697/6677/4326） |
| shapely | BSD-3-Clause | 幾何演算（外形・内包判定） |
| scipy | BSD-3-Clause | 近傍探索（用途ベースライン） |
| python-dotenv | BSD-3-Clause | `.env` の読み込み |
| pyyaml | MIT | 設定・コードリストの読み込み |
| overturemaps | MIT | Overture Maps Places の取得 CLI |
| pytest | MIT | テスト |

## フロントエンド（JavaScript）

| ライブラリ | ライセンス | 取得方法 | 用途 |
| --- | --- | --- | --- |
| MapLibre GL JS | BSD-3-Clause | CDN（unpkg） | 地図表示 |
| @mediapipe/tasks-vision（BlazeFace） | Apache-2.0 | `web/vendor/` に同梱（オフライン用） | 現地写真の顔の自動ぼかし（端末内推論） |
| qrcode-generator（Kazuhiko Arase） | MIT | `web/vendor/qrcode/` に同梱（CDN 非依存） | 調査計画の受け渡し URL を QR コード化（PC→現地スマホ） |

## 外部サービス

| サービス | ライセンス / 規約 | 用途 |
| --- | --- | --- |
| OSRM（公開デモサーバー / 自前運用） | BSD-2-Clause（経路データは OpenStreetMap, ODbL） | 現地調査の徒歩経路。座標のみ送信・保存しない |
| 国土地理院 地理院タイル | 出典明示 | 背景地図 |

> 注：MediaPipe の WASM / モデルは CDN ではなく自サーバー（`web/vendor/mediapipe/`）から配信する。
> 写真はサーバーに送らず端末内で推論する（詳細は `web/vendor/mediapipe/README.md`）。
