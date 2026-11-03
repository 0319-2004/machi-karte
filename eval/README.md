# eval

既知棟での精度検証を行う場所。精度などの数字は、ここのスクリプトで再現できる形で報告する。

- `holdout.py` … 既知棟の定義とホールドアウト分割（seed 固定、`eval_split` に保存）
- `common.py` … 高さ帯・不明群重み・近傍モデル（train のみから作る）の共通部品
- `baselines.py` … ベースライン (a)(b)(c)。`run_bands()` で高さ帯別＋不明群重み付き
- `poi_eval.py` … POI 結合の効き目（被覆率・正解率・近傍への上積み、すべて高さ帯別）

再現：`uv run python -m eval.holdout` → `uv run python -m eval.baselines` → `uv run python -m eval.poi_eval`。
