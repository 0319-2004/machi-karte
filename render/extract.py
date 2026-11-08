"""PLATEAU 建築物 CityGML から Blender 用のメッシュ素材を抜き出す。

- LOD2（壁・屋根・接地面・付属物）がある棟は LOD2 を、無い棟は LOD1 立体を使う
- 座標は EPSG:6697（緯度・経度・標高）→ 平面直角第9系 EPSG:6677（m）へ変換し、
  原点を都庁付近に寄せた局所座標で保存する（Blender の浮動小数の精度対策）
- 面ごとに「種類（壁/屋根/接地/付属）」と「棟番号」を持たせ、棟ごとに用途・高さ・
  区コードを持たせる。色分け（不明群など）は描画側で行う

出力：data/derived/render/meshes.npz（git 管理外）

使い方：
    uv run python -m render.extract            # data/raw/plateau/2025/udx/bldg/*.gml を全部
    uv run python -m render.extract --mesh 53394525 53394526
"""
from __future__ import annotations

import argparse
import glob
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from lxml import etree
from pyproj import Transformer

BLDG = "{http://www.opengis.net/citygml/building/2.0}"
GML = "{http://www.opengis.net/gml}"

# 局所座標の原点（東京都庁第一本庁舎付近、lon/lat）
ORIGIN_LONLAT = (139.69171, 35.68949)

KIND_WALL, KIND_ROOF, KIND_GROUND, KIND_INSTALL = 0, 1, 2, 3
_SURFACE_KIND = {
    "WallSurface": KIND_WALL,
    "RoofSurface": KIND_ROOF,
    "GroundSurface": KIND_GROUND,
    "OuterCeilingSurface": KIND_WALL,
    "OuterFloorSurface": KIND_ROOF,
    "ClosureSurface": KIND_WALL,
}

_TO_6677 = Transformer.from_crs("EPSG:6668", "EPSG:6677", always_xy=True)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _polys_under(el):
    """要素配下の gml:Polygon 外周リングを (n,3) の (lat, lon, h) 配列で返す。"""
    out = []
    for pos in el.iter(f"{GML}posList"):
        # 内周（穴）は描画上の影響が小さいので外周のみ使う
        if _local(pos.getparent().getparent().tag) != "exterior":
            continue
        a = np.array(pos.text.split(), dtype=np.float64).reshape(-1, 3)
        if len(a) >= 4 and np.allclose(a[0], a[-1]):
            a = a[:-1]
        if len(a) >= 3:
            out.append(a)
    return out


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def parse_file(path: str) -> dict:
    """1ファイルを解析し、面と棟の配列を返す（座標は lat/lon/h のまま）。"""
    polys, kinds, owner = [], [], []
    b_id, b_usage, b_height, b_lod = [], [], [], []

    ctx = etree.iterparse(path, events=("end",), tag=f"{BLDG}Building")
    for _, b in ctx:
        bid, usage, height = "", "", ""
        for c in b:
            ln = _local(c.tag)
            if ln == "usage" and not usage:
                usage = _text(c)
            elif ln == "measuredHeight":
                height = _text(c)
            elif ln == "buildingIDAttribute":
                bid = _text(c.find("{*}BuildingIDAttribute/{*}buildingID"))

        idx = len(b_id)
        got = []
        # LOD2：境界面（壁・屋根・接地）と付属物
        for bb in b.findall(f"{BLDG}boundedBy"):
            for surf in bb:
                k = _SURFACE_KIND.get(_local(surf.tag), KIND_WALL)
                for p in _polys_under(surf):
                    got.append((p, k))
        for inst in b.iter(f"{BLDG}BuildingInstallation"):
            for p in _polys_under(inst):
                got.append((p, KIND_INSTALL))
        lod = "LOD2" if got else "LOD1"
        if not got:
            solid = b.find(f"{BLDG}lod1Solid")
            if solid is not None:
                for p in _polys_under(solid):
                    got.append((p, -1))  # 向きから壁/屋根/接地を判定する
        if got:
            b_id.append(bid)
            b_usage.append(usage)
            b_height.append(float(height) if height else np.nan)
            b_lod.append(lod)
            zmin = p_min_z(got)
            for p, k in got:
                if k == -1:
                    dz = p[:, 2].max() - p[:, 2].min()
                    if dz > 0.01:
                        k = KIND_WALL
                    else:
                        k = KIND_ROOF if p[0, 2] > zmin + 0.01 else KIND_GROUND
                polys.append(p)
                kinds.append(k)
                owner.append(idx)

        b.clear()
        while b.getprevious() is not None:
            del b.getparent()[0]

    return {
        "polys": polys,
        "kinds": kinds,
        "owner": owner,
        "b_id": b_id,
        "b_usage": b_usage,
        "b_height": b_height,
        "b_lod": b_lod,
    }


def p_min_z(got) -> float:
    return min(float(p[:, 2].min()) for p, _ in got)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/raw/plateau/2025/udx/bldg")
    ap.add_argument("--mesh", nargs="*", help="対象の3次メッシュコード（省略で全ファイル）")
    ap.add_argument("--out", default="data/derived/render/meshes.npz")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.src, "*_bldg_*.gml")))
    if args.mesh:
        files = [f for f in files if os.path.basename(f).split("_")[0] in set(args.mesh)]
    if not files:
        raise SystemExit(f"GML が見つからない：{args.src}")
    print(f"{len(files)} ファイルを解析する")

    ox, oy = _TO_6677.transform(*ORIGIN_LONLAT)
    verts, f_len, f_kind, f_owner = [], [], [], []
    b_id, b_usage, b_height, b_lod = [], [], [], []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for path, r in zip(files, ex.map(parse_file, files)):
            base = len(b_id)
            print(f"  {os.path.basename(path)}: 棟 {len(r['b_id'])}, 面 {len(r['polys'])}")
            if not r["polys"]:
                continue
            allp = np.concatenate(r["polys"])
            x, y = _TO_6677.transform(allp[:, 1], allp[:, 0])
            verts.append(np.column_stack([np.asarray(x) - ox, np.asarray(y) - oy, allp[:, 2]]).astype(np.float32))
            f_len.append(np.array([len(p) for p in r["polys"]], dtype=np.int32))
            f_kind.append(np.array(r["kinds"], dtype=np.uint8))
            f_owner.append(np.array(r["owner"], dtype=np.int32) + base)
            b_id += r["b_id"]
            b_usage += r["b_usage"]
            b_height += r["b_height"]
            b_lod += r["b_lod"]

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    np.savez_compressed(
        args.out,
        verts=np.concatenate(verts),
        face_len=np.concatenate(f_len),
        face_kind=np.concatenate(f_kind),
        face_owner=np.concatenate(f_owner),
        b_id=np.array(b_id),
        b_usage=np.array(b_usage),
        b_height=np.array(b_height, dtype=np.float32),
        b_lod=np.array(b_lod),
        origin_lonlat=np.array(ORIGIN_LONLAT),
    )
    n13104 = sum(1 for i in b_id if i.startswith("13104-"))
    print(f"保存：{args.out}（棟 {len(b_id)}、うち新宿区 {n13104}、面 {sum(len(a) for a in f_len)}）")


if __name__ == "__main__":
    main()
