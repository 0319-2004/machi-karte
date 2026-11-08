# render

PLATEAU 新宿区（13104）の建物を Blender（Cycles）で静止画にする場所。
発表スライド・README 用の「絵」を、取り込みと同じ CityGML から再現できる形で作る。
画像は `data/derived/render/`（git 管理外）に書き出す。

## 流れ

1. `render/extract.py` … `data/raw/plateau/2025/udx/bldg/*.gml` を読み、LOD2（壁・屋根・付属物）がある棟は LOD2、
   無い棟は LOD1 立体で面を取り出して `data/derived/render/meshes.npz` に保存する。
   座標は EPSG:6697 → EPSG:6677 に変換し、都庁付近を原点にした局所座標（m）で持つ。
2. `render/scene.py` … npz を読み、カットごとに範囲・色分け・光・カメラを設定して Cycles（CPU）で描く。
   地面は棟の接地標高を Delaunay でつないだ簡易地形（scipy が無ければ平面）。

## カット

| カット | 内容 | 色分け |
| --- | --- | --- |
| `nishi_shinjuku` | 西新宿の超高層ビル群（LOD2）を南西上空から、夕方の斜光 | 白い模型 |
| `kabukicho_unknown` | 新宿駅東口〜歌舞伎町を東上空から | 白＋不明群（usage=461）を橙 |
| `ward_height` | 新宿区全域を南西上空から | 高さで段階色 |
| `ward_unknown` | 新宿区全域（暗い地） | 既知棟は青灰、不明群を発光する橙 |
| `ward_topdown` | 新宿区全域を真上から（正射影、上＝北） | 同上 |

## 動画

`render/anim.py` は、カットのカメラを始点→終点へゆっくり動かし、途中で不明群を白→橙に灯す動画を作る。
コマを `data/derived/render/anim/<名前>/####.png` に書き出し、ffmpeg で `data/derived/render/<名前>.mp4` にまとめる。
書き出し済みのコマは飛ばすので、止めても続きから再開できる。

| 動画 | 内容 |
| --- | --- |
| `kabukicho_reveal` | `kabukicho_unknown` のカメラで 8 秒（24fps、1280×720）。1.6〜3.4 秒で不明群が灯る |

```bash
make render-anim
```

1コマ約23秒（8 サンプル＋デノイズ、反射2回、CPU 4 コア）で、8 秒（192 コマ）に約75分。

件数（全棟・不明群）は描画のたびにデータから数えてログに出す（コードに直書きしない）。
全域カットは区外棟（メッシュタイルに混在する隣接区）を出さない。

## 使い方

`bpy`（Blender の Python モジュール）は Python 3.13 用なので、プロジェクトの uv 環境（3.12）とは別の venv に入れる。

```bash
uv venv -p 3.13 .venv-render
```

```bash
uv pip install -p .venv-render bpy==5.2.3 numpy lxml pyproj scipy
```

```bash
make render
```

個別に動かす場合：

```bash
.venv-render/bin/python -m render.extract
```

```bash
.venv-render/bin/python -m render.scene --shot nishi_shinjuku --samples 128
```

下見は `--scale 40 --samples 16`（数十秒/枚）。Blender 本体があれば `blender -b -P render/scene.py -- --shot all` でも動く。

## 帰属

画像には PLATEAU（国土交通省 Project PLATEAU）の 3D 都市モデルを使っている。画像を載せる場所には出典を明示する。
