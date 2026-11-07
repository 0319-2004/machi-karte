# QR コード生成（調査計画の受け渡し用）

PC の地図で選んだ調査計画を現地スマホへ渡すとき、受け渡し URL を **QR コード**にする。
CDN ではなく、ここ（アプリが `/vendor/qrcode/` で配信）から読み込む
＝**デモ中に通信が不安定でも動く**。

## ライセンス

- **MIT License**（© 2009 Kazuhiko Arase）。本リポジトリの MIT と整合する。
- ライブラリ名：`qrcode-generator`（npm / <http://www.d-project.com/>）。
- 帰属表示は `docs/attribution.md` と `docs/licenses.md` にも記載。

## ファイル

リポジトリに含める（小さい・約56KB）：

- `qrcode.js` … QR コード生成本体（グローバル `qrcode(typeNumber, ecLevel)` を公開）

CDN には依存しない（上記ファイルをそのまま配信する）。大きい取得物は無い。

## 使い方（`web/app.js`）

```js
const qr = qrcode(0, "M");      // 0=型番自動、M=誤り訂正レベル中
qr.addData(url);                // 受け渡し URL（計画IDのみ。属性・位置は含めない）
qr.make();
box.innerHTML = qr.createSvgTag({ cellSize: 6, margin: 2, scalable: true });
```

## 更新するとき

別バージョンに差し替える場合は、同じ MIT（または BSD）であることを確認し、
`qrcode.js` を置き換えたうえで本 README と `docs/licenses.md` を合わせて更新する。

```
curl -sSL -o web/vendor/qrcode/qrcode.js \
  https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js
```
