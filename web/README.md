# web

MapLibre GL JS による地図と確認画面を置く場所。まずはビルド不要の素の HTML/JS で始める。
背景地図は国土地理院タイル（出典表示を必ず出す）。

- `index.html` … 画面の骨組み（件数カウンタ・優先度リスト・属性パネル・帰属表示リンク）。
- `app.js` … 地図（地理院 淡色＋`fill-extrusion`）、全棟⇄調査対象のみの切替アニメ、件数カウントダウン、
  リストから `flyTo`、クリックで属性パネル。API は `/api/*`、建物は `/data/buildings.geojson`。
- `style.css` … レイアウト。MapLibre GL JS は CDN（unpkg）から読み込む（ビルド不要）。

`api`（FastAPI）が `/` で配信するので、`make serve` 後に http://localhost:8000/ を開く。
