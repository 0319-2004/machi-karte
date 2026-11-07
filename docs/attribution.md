# 帰属表示

このファイルと、画面の帰属ページ、出力ファイルにそろえて載せる。

| データ源 | 表記 |
| --- | --- |
| PLATEAU | 出典明示（国土交通省 Project PLATEAU） |
| OpenStreetMap | © OpenStreetMap contributors（ODbL） |
| Overture Maps | CDLA Permissive 2.0。Foursquare 由来の項目は NOTICE を添える |
| 東京都デジタルツイン | CC BY 4.0 |
| 国土地理院 | 出典明示（地理院タイル。調査プランのグループ地名は地理院リバースジオコーダ） |
| OSRM | BSD-2-Clause。現地調査の徒歩経路の取得に使用（公開サーバー `router.project-osrm.org`。本番は自前運用）。経路データは OpenStreetMap（ODbL）由来 |
| MediaPipe Face Detector | Apache-2.0（© The MediaPipe Authors）。現地写真の顔の自動ぼかしに使用。モデルは BlazeFace。推論は端末内で行い、画像はサーバーに送らない |
| qrcode-generator | MIT（© 2009 Kazuhiko Arase）。調査計画の受け渡し URL を QR コード化するのに使用。CDN ではなく自サーバー（`/vendor/qrcode/`）から配信 |

## ライセンス方針

- **このリポジトリのコードは MIT ライセンス**（[`LICENSE`](../LICENSE)）。
- **`data/` はリポジトリに含めない。** 生データ・派生データ・キャッシュは各自で取得し、各データ源のライセンスにそれぞれ従う（配置は [`data/README.md`](../data/README.md) を参照）。
- **出力ファイル（更新候補リスト）には、使ったデータ源のライセンスが及ぶ。** 特に `source` が `poi` / `osm_building` の行は OpenStreetMap 由来の情報を含むため、**ODbL（Open Database License）の対象になり得る**。出力を配布・公開する場合は ODbL の帰属・継承義務に従うこと。
  - なお `source` が `neighbor` の行は PLATEAU の既知棟（近傍）由来であり、OSM の対象ではない。`height` / `version_diff` も PLATEAU 由来。
- **徒歩経路の取得には OSRM を使う。** デモは公開サーバー（`router.project-osrm.org`、BSD-2-Clause）を使うが、本番は自前サーバーを立てる前提。**現在地（座標）は経路取得のためだけに使い、保存しない。OSRM に送るのは座標のみ。** OSRM の経路は OpenStreetMap（ODbL）由来なので、経路ジオメトリを配布・公開する場合は ODbL の帰属・継承義務に従うこと。
- **顔の自動ぼかしは MediaPipe Face Detector（Apache-2.0, © The MediaPipe Authors）** を使う。ローダ・モデル・WASM は **CDN ではなく自サーバー（`/vendor/mediapipe/`）から配信**し、**写真そのものはサーバーに送らず端末内で推論**する（詳細は [`web/vendor/mediapipe/README.md`](../web/vendor/mediapipe/README.md)）。Apache-2.0 は本リポジトリの MIT とも、ODbL・CC BY-SA のデータ/画像との併用とも整合する（コピーレフト・非商用の制約は無い）。
- **調査計画の QR コード生成は qrcode-generator（MIT, © 2009 Kazuhiko Arase）** を使う。**CDN ではなく自サーバー（`/vendor/qrcode/`）から配信**する（デモ中に通信が不安定でも動くように）。QR には受け渡し URL（計画IDのみ）を載せ、建物属性や位置情報は含めない。
