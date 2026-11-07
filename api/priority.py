"""不明群の優先度スコア（設計書「優先度の付け方」）。

担当者が何から見るかを決める並び。効く順は

    1. 高さが大きい棟     （影響が大きく、画像も得やすい）
    2. 新しく現れた棟     （版間差分 source='version_diff' が付く棟）
    3. 外形面積が大きい棟
    4. 画像がある棟       （LOD2テクスチャの有無）

これを「各要素を[0,1]に正規化し、重みを段階的に下げた加重和」にする。
重みの大小が設計書の優先順に一致する（式の導出は docs/decisions.md）。

    score = 0.60·height_n + 0.25·is_new + 0.10·area_n + 0.05·has_image
    height_n = min(height, 100) / 100      （100m で頭打ち。欠損は0）
    area_n   = min(area_m2, 1000) / 1000    （外形面積 ㎡, EPSG:6677。1000㎡で頭打ち）
    is_new   = 版間差分で新規なら1
    has_image= LOD2テクスチャありなら1
"""
from __future__ import annotations

# スコアの重み（高さ > 新規 > 面積 > 画像）。合計1.0。
W_HEIGHT, W_NEW, W_AREA, W_IMAGE = 0.60, 0.25, 0.10, 0.05
HEIGHT_CAP, AREA_CAP = 100.0, 1000.0

# SELECT 用のスコア式。buildings b と new_ids n（版間差分の新規ID）を前提にする。
SCORE_SQL = f"""
  {W_HEIGHT} * LEAST(COALESCE(b.height_m, 0), {HEIGHT_CAP}) / {HEIGHT_CAP}
+ {W_NEW}    * (CASE WHEN n.building_id IS NOT NULL THEN 1 ELSE 0 END)
+ {W_AREA}   * LEAST(COALESCE(ST_Area(b.geom), 0), {AREA_CAP}) / {AREA_CAP}
+ {W_IMAGE}  * (CASE WHEN b.lod_type = 'LOD2' THEN 1 ELSE 0 END)
"""

# デモの開始点を固定する。西新宿（都庁から半径1km, EPSG:6677 の円）の棟を先頭に寄せ、
# その中は score 降順、最後に building_id で完全に決定づける（毎回同じ並び＝再現可能）。
# 初期表示も都庁（139.6917, 35.6896）なので、画面の位置と優先度トップが一致する。
NISHI_FLAG_SQL = """
COALESCE(
  ST_Distance(
    ST_Centroid(b.geom),
    ST_Transform(ST_Point(139.6917, 35.6896), 'EPSG:4326', 'EPSG:6677', true)
  ) <= 1000, FALSE)
"""

TARGETS_SQL = f"""
WITH new_ids AS (
    SELECT DISTINCT building_id FROM candidates
    WHERE version = ? AND source = 'version_diff'
)
SELECT b.building_id, b.height_m, b.storeys_above, b.usage,
       ST_X(ST_Centroid(b.geom_4326)), ST_Y(ST_Centroid(b.geom_4326)),
       ST_Area(b.geom),
       (n.building_id IS NOT NULL) AS is_new,
       (b.lod_type = 'LOD2')       AS has_image,
       ({SCORE_SQL})               AS score,
       EXISTS (SELECT 1 FROM confirmations c
               WHERE c.building_id = b.building_id AND c.version = b.version) AS confirmed
FROM buildings b
LEFT JOIN new_ids n ON n.building_id = b.building_id
WHERE b.version = ? AND b.is_target AND b.geom_4326 IS NOT NULL
ORDER BY confirmed ASC, ({NISHI_FLAG_SQL}) DESC, score DESC, b.building_id
LIMIT ?
"""


def demo_counts(con, version: str = "2025") -> dict:
    """デモ範囲の候補棟数：高さ20m以上（設計書196）と西新宿周辺。

    西新宿は都庁（139.6917, 35.6896）から半径1kmの円（EPSG:6677, m）で定義する。
    """
    h20 = con.execute(
        "SELECT count(*) FROM buildings WHERE version=? AND is_target AND height_m >= 20",
        [version],
    ).fetchone()[0]
    # 都庁を 6677 に投影し、不明群の重心との距離が1km以内を数える
    nishi = con.execute(
        """
        SELECT count(*) FROM buildings
        WHERE version=? AND is_target AND geom IS NOT NULL
          AND ST_Distance(
                ST_Centroid(geom),
                ST_Transform(ST_Point(139.6917, 35.6896), 'EPSG:4326', 'EPSG:6677', true)
              ) <= 1000
        """,
        [version],
    ).fetchone()[0]
    nishi_h20 = con.execute(
        """
        SELECT count(*) FROM buildings
        WHERE version=? AND is_target AND height_m >= 20 AND geom IS NOT NULL
          AND ST_Distance(
                ST_Centroid(geom),
                ST_Transform(ST_Point(139.6917, 35.6896), 'EPSG:4326', 'EPSG:6677', true)
              ) <= 1000
        """,
        [version],
    ).fetchone()[0]
    return {"h20": h20, "nishishinjuku": nishi, "nishishinjuku_h20": nishi_h20}
