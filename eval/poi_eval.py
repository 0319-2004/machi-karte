"""POI結合の効き目を既知棟ホールドアウトで測る（Phase 2 後半・step 3/4）。

測る（すべて高さ帯別にも）:
    - POI が付いた棟の割合、付いた棟での正解率
    - POI あり→POI、なし→近傍(c) で埋める合成の正解率
    - 不明群(is_target)で POI / OSM建物タグが付く棟の割合
    - 近傍の最良（(c) k近傍＋高さ）より POI が上積みになる帯・ならない帯

近傍(c)はベースライン band と同じ定義（k近傍＋高さ±3m、train のみ）。
POI 予測は estimate.poi の compute() を使う（building_id 由来、usage は参照しない）。
"""
from __future__ import annotations

import numpy as np

from estimate.poi import compute
from .baselines import BAND_NEIGHBOR_K, HEIGHT_TOL
from .common import (BAND_LABELS, NeighborModel, connect, load_known,
                     target_band_weights, weighted_accuracy)


def _acc(pred, truth, mask):
    """mask 内での正解率(%)。mask が空なら None。"""
    m = mask & np.array([p is not None for p in pred])
    if m.sum() == 0:
        return None
    return float(np.mean(pred[m] == truth[m])) * 100


def _by_band(bands, fn):
    """帯ごとに fn(mask) を呼んで dict を返す。"""
    out = {}
    for label in BAND_LABELS:
        out[label] = fn(bands == label)
    out["_all"] = fn(np.ones(len(bands), dtype=bool))
    return out


def _fmt(x):
    return "   -  " if x is None else f"{x:5.1f}%"


def run(k: int = BAND_NEIGHBOR_K) -> dict:
    con = connect()
    feats = compute(con)["feats"]
    d = load_known(con)
    tr = d["split"] == "train"
    ho = d["split"] == "holdout"
    model = NeighborModel(d["xy"][tr], d["usage"][tr], d["height"][tr],
                          train_id=d["building_id"][tr])

    ho_id = d["building_id"][ho]
    ho_usage = d["usage"][ho]
    ho_xy = d["xy"][ho]
    ho_height = d["height"][ho]
    ho_band = d["band"][ho]
    n = len(ho_usage)

    # 近傍(c)：k近傍＋高さ±3m（これが POI が超えるべき物差し）
    nbr_c = model.predict_many_knn(ho_xy, k, heights=ho_height, height_tol=HEIGHT_TOL)

    # POI / OSM建物タグ の予測
    poi_pred = np.array([feats.get(b, {}).get("poi_pred") for b in ho_id], dtype=object)
    osmb_pred = np.array([feats.get(b, {}).get("osmb_pred") for b in ho_id], dtype=object)
    has_poi = np.array([p is not None for p in poi_pred])
    has_osmb = np.array([p is not None for p in osmb_pred])

    # 合成1：POIあり→POI、なし→近傍(c)
    comp_poi = np.where(has_poi, poi_pred, nbr_c)
    # 合成2：POI優先→OSM建物タグ→近傍(c)
    comp_both = np.where(has_poi, poi_pred,
                         np.where(has_osmb, osmb_pred, nbr_c))

    weights = target_band_weights(con)

    def wacc(band_dict):
        return weighted_accuracy(
            {k2: v for k2, v in band_dict.items() if k2 != "_all"}, weights)

    # --- 被覆率 ---
    cov_poi = _by_band(ho_band, lambda m: float(np.mean(has_poi[m])) * 100 if m.sum() else None)
    cov_osmb = _by_band(ho_band, lambda m: float(np.mean(has_osmb[m])) * 100 if m.sum() else None)

    # --- 正解率 ---
    acc_poi = _by_band(ho_band, lambda m: _acc(poi_pred, ho_usage, m & has_poi))
    acc_osmb = _by_band(ho_band, lambda m: _acc(osmb_pred, ho_usage, m & has_osmb))
    acc_nbr = _by_band(ho_band, lambda m: _acc(nbr_c, ho_usage, m))
    acc_comp = _by_band(ho_band, lambda m: _acc(comp_poi, ho_usage, m))
    acc_both = _by_band(ho_band, lambda m: _acc(comp_both, ho_usage, m))

    print(f"=== POI 評価（既知棟 holdout={n}, 近傍(c) k={k}＋高さ±{HEIGHT_TOL:g}m）===")
    print("\n[被覆率] POI/OSM建物タグ が付いた holdout 棟の割合")
    print(f"  {'帯':<9}{'POI':>8}{'OSM建物':>9}")
    for b in BAND_LABELS + ["_all"]:
        print(f"  {b:<9}{_fmt(cov_poi[b]):>8}{_fmt(cov_osmb[b]):>9}")

    print("\n[正解率] 帯別（付いた棟のみ / 合成は全棟）")
    hdr = f"  {'帯':<9}{'近傍(c)':>9}{'POIのみ':>9}{'合成POI':>9}{'上積み':>9}" \
          f"{'OSM建物':>9}{'合成+OSM':>9}"
    print(hdr)
    for b in BAND_LABELS + ["_all"]:
        lift = (None if acc_comp[b] is None or acc_nbr[b] is None
                else acc_comp[b] - acc_nbr[b])
        lifts = "   -  " if lift is None else f"{lift:+5.1f}"
        print(f"  {b:<9}{_fmt(acc_nbr[b]):>9}{_fmt(acc_poi[b]):>9}"
              f"{_fmt(acc_comp[b]):>9}{lifts:>9}{_fmt(acc_osmb[b]):>9}"
              f"{_fmt(acc_both[b]):>9}")

    # 不明群の重みで加重
    wl = (f"  {'不明群重み':<9}{_fmt(wacc(acc_nbr)):>9}{_fmt(wacc(acc_poi)):>9}"
          f"{_fmt(wacc(acc_comp)):>9}")
    wlift = wacc(acc_comp) - wacc(acc_nbr)
    print(wl + f"{wlift:+8.1f} {_fmt(wacc(acc_osmb)):>8}{_fmt(wacc(acc_both)):>9}")
    print("  （重み＝不明群の高さ帯構成比：" +
          " ".join(f"{b}={weights[b]*100:.0f}%" for b in BAND_LABELS) + "）")

    # --- 不明群(is_target)での被覆率 ---
    print("\n[不明群] is_target で POI/OSM建物タグ が付く割合（高さ帯別）")
    tgt = con.execute(
        "SELECT building_id, height_m FROM buildings "
        "WHERE version=? AND is_target", ["2025"],
    ).fetchall()
    from .common import band_of
    tgt_band = np.array([band_of(h) if h is not None else None for _, h in tgt])
    tgt_has_poi = np.array([feats.get(b, {}).get("poi_pred") is not None for b, _ in tgt])
    tgt_has_osmb = np.array([feats.get(b, {}).get("osmb_pred") is not None for b, _ in tgt])
    print(f"  {'帯':<9}{'棟数':>7}{'POI':>8}{'OSM建物':>9}{'いずれか':>9}")
    for b in BAND_LABELS + [None]:
        m = (tgt_band == b) if b is not None else np.array([x is None for x in tgt_band])
        cnt = int(m.sum())
        if cnt == 0:
            continue
        cp = np.mean(tgt_has_poi[m]) * 100
        co = np.mean(tgt_has_osmb[m]) * 100
        ce = np.mean(tgt_has_poi[m] | tgt_has_osmb[m]) * 100
        label = b if b is not None else "高さ無"
        print(f"  {label:<9}{cnt:>7}{cp:>7.1f}%{co:>8.1f}%{ce:>8.1f}%")
    allm = np.ones(len(tgt), dtype=bool)
    print(f"  {'全体':<9}{len(tgt):>7}{np.mean(tgt_has_poi)*100:>7.1f}%"
          f"{np.mean(tgt_has_osmb)*100:>8.1f}%"
          f"{np.mean(tgt_has_poi|tgt_has_osmb)*100:>8.1f}%")

    con.close()
    return {
        "acc_nbr": acc_nbr, "acc_poi": acc_poi, "acc_comp": acc_comp,
        "acc_osmb": acc_osmb, "acc_both": acc_both,
        "cov_poi": cov_poi, "cov_osmb": cov_osmb, "weights": weights,
    }


if __name__ == "__main__":
    run()
