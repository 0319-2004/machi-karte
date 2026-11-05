# export

自治体向けの更新候補リスト（CSV / GeoJSON）を出力する場所。
人が確定した推定値のみを出す（未確定・判断不能は出さない）。

---

以下は、出力物（更新候補リスト）に同梱する **README テンプレート**（Phase 9 で使う）。
出力を配布・公開するときは、このテンプレートをコピーして出力ディレクトリに添える。

<!-- ここから出力同梱用テンプレート -->

# まちカルテ 更新候補リスト

PLATEAU 新宿区（13104）の建物属性について、人が地図で確認・確定した更新候補です。
未確定・判断不能の属性は含みません。

## 帰属表示

| データ源 | 表記 |
| --- | --- |
| PLATEAU | 出典明示（国土交通省 Project PLATEAU） |
| OpenStreetMap | © OpenStreetMap contributors（ODbL） |
| Overture Maps | CDLA Permissive 2.0。Foursquare 由来の項目は NOTICE を添える |
| Mapillary | ロゴとリンクを表示。画像ごとに「by *username*, CC BY-SA」 |
| 東京都デジタルツイン | CC BY 4.0 |
| 国土地理院 | 出典明示（地理院タイル） |

## ライセンスの注記

- この更新候補リストには、使ったデータ源のライセンスが及びます。
- **`source` が `poi` / `osm_building` の行は OpenStreetMap 由来の情報を含むため、ODbL（Open Database License）の対象になり得ます。** 配布・公開する場合は ODbL の帰属・継承義務に従ってください。
- **Mapillary の画像はこの出力に含まれません。** 参照は画像 URL のみです。画面に表示する場合は帰属表示（ロゴ・リンク、画像ごとに「by *username*, CC BY-SA」）を付けてください。

## ファイル

| ファイル | 内容 |
| --- | --- |
| `update_candidates_<city>.csv` | 更新候補（UTF-8 BOM 付き。Excel でそのまま開けます） |
| `update_candidates_<city>.geojson` | 同じ内容を建物外形付き（EPSG:4326）で。GIS に取り込めます |

## PLATEAU 属性との対応（取り込みの目安）

この CSV / GeoJSON は、PLATEAU の次の属性に対応する**更新候補（差分の提案）**です。
**公式データへ直接書き込むものではありません。** 自治体側で内容を確認のうえ反映してください。

| 出力の `attribute` | PLATEAU 属性 | 出力列の対応 |
| --- | --- | --- |
| `usage` | `bldg:usage` | `current_value`=現在のコード（`461`=不明）→ `proposed_value`=確定コード、`proposed_label`=名称 |
| `storeys_above` | `bldg:storeysAboveGround` | `current_value`=現在値（`9999`=不明）→ `proposed_value`=階数 |
| `year_built` | `uro:yearOfConstruction` | 区間で提案。`proposed_value`=`2022-2024`、`value_min`／`value_max` を別列で |
| （建物の同定） | `uro:buildingID` | `building_id`（例 `13104-bldg-2923`）。外部との唯一のキー |

`source` は推定の由来（`neighbor`／`height`／`version_diff`／`poi`／`osm_building`／`image`、
`manual`＝人が修正）。`confidence` は推定時の信頼度、`reasoning` は根拠の文章です。
`confirmed_via` は確定の経路で、`desk`（机上の確認画面）か `onsite`（スマホの現地調査）です。
`source` が推定の出どころなのに対し、`confirmed_via` は誰がどこで確定したかを表します。

<!-- ここまで出力同梱用テンプレート -->
