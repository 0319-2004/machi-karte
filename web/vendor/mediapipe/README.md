# MediaPipe（顔の自動ぼかし用）

現地写真の顔を**端末内で**自動ぼかしするために MediaPipe Face Detector を使う。
CDN ではなく、ここ（アプリが `/vendor/mediapipe/` で配信）から読み込む
＝**デモ中に通信が不安定でも動く**。

## ライセンス

- ソフトウェア・モデルとも **Apache License 2.0**（© The MediaPipe Authors / Google）。
  本リポジトリの MIT とも、ODbL・CC BY-SA のデータ/画像との併用とも整合する。
- 出自：npm `@mediapipe/tasks-vision`（ローダ/WASM）と Google 配布の
  `blaze_face_short_range`（BlazeFace 短距離モデル）。
- 帰属表示は `docs/attribution.md` と画面の `/attribution` にも記載。

## ファイル

リポジトリに含める（小さい・合計 約760KB）：

- `vision_bundle.mjs` … tasks-vision の ESM 本体（約137KB）
- `wasm/vision_wasm_internal.js` / `wasm/vision_wasm_nosimd_internal.js` … WASM ローダ（各約200KB）
- `blaze_face_short_range.tflite` … 顔検出モデル（約224KB）

git 管理外（大きい・各約9MB、`.gitignore` 済み）：

- `wasm/vision_wasm_internal.wasm`（SIMD）
- `wasm/vision_wasm_nosimd_internal.wasm`（SIMD 非対応端末向け）

## WASM の取得

```
make fetch-mediapipe
```

未取得でも**自動ぼかしを飛ばして手塗りに進む**（アプリは止まらない）。
バージョンを変えるときは `make fetch-mediapipe MP_VER=0.10.x` と、`web/survey.js` の
`MP_VER`、本 README を合わせて更新する。
