"""PLATEAU 新宿区の建物を Blender（Cycles）で静止画にレンダリングする。

render/extract.py が作った data/derived/render/meshes.npz を読み、カット（SHOTS）ごとに
範囲を切り出して、色分け・光・カメラを設定して PNG を書き出す。

色分けのモード：
- clay     … 白い模型風（屋根を少し明るく）
- height   … 新宿区の棟を高さで塗り分け（区外は淡いグレー）
- unknown  … 用途「不明」（usage=461、不明群）を橙で強調し、他は白

使い方（bpy モジュール、または Blender 本体のどちらでも動く。詳細は render/README.md）：
    python -m render.scene --shot nishi_shinjuku
    blender -b -P render/scene.py -- --shot all --samples 128
カット一覧と色分けの対応は render/README.md を参照。
出力：data/derived/render/<shot>.png（git 管理外）
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import bpy
import numpy as np
from mathutils import Vector

KIND_WALL, KIND_ROOF, KIND_GROUND, KIND_INSTALL = 0, 1, 2, 3
UNKNOWN_USAGE = "461"  # 用途コード表 Building_usage.xml で「不明」

# --- カット定義（座標は都庁付近を原点とする EPSG:6677 の局所座標、m。z は接地標高の中央値からの高さ） ---
# bbox は (xmin, ymin, xmax, ymax)：この範囲に重心がある棟だけを載せる
# ward_only：新宿区（13104）の棟だけを載せる（メッシュタイル四角の区外棟を出さない）
# style：day（昼の模型風）/ night（暗い地に建物を浮かせる地図風）
SHOTS = {
    # 西新宿の超高層ビル群（LOD2）を南西の上空から、夕方の斜光で
    "nishi_shinjuku": dict(
        mode="clay", style="dusk",
        bbox=(-2600, -2600, 2600, 2600),
        cam=(-820, -1050, 420),
        target=(180, 260, 90),
        lens=40,
        sun=dict(elevation=11, azimuth=292, strength=6.0),
        fog=0.00016,
        res=(1920, 1080),
    ),
    # 新宿駅東口〜歌舞伎町を東の上空から。不明群（橙）が密集地のどこにあるか
    "kabukicho_unknown": dict(
        mode="unknown", style="day",
        bbox=(-600, -1600, 3200, 2600),
        cam=(1950, -520, 380),
        target=(980, 420, 40),
        lens=35,
        sun=dict(elevation=34, azimuth=215, strength=6.0),
        fog=0.00014,
        res=(1920, 1080),
    ),
    # 新宿区全域を南西の上空から、高さで塗り分け
    "ward_height": dict(
        mode="height", style="night", ward_only=True,
        bbox=None,
        cam=(-2500, -3500, 2300),
        target=(1250, 1500, 0),
        lens=29,
        sun=dict(elevation=28, azimuth=235, strength=4.0),
        fog=0.00008,
        res=(1920, 1080),
    ),
    # 新宿区全域：57,780 棟のうち不明群 4,982 棟だけを橙に（件数は描画時にデータから数える）
    "ward_unknown": dict(
        mode="unknown", style="night", ward_only=True,
        bbox=None,
        cam=(1500, -5000, 2700),
        target=(1500, 1350, 0),
        lens=36,
        sun=dict(elevation=40, azimuth=210, strength=1.6),
        fog=0.0001,
        res=(1920, 1080),
    ),
    # 真上からの図（正射影）：新宿区全域の図と地、不明群を橙で
    "ward_topdown": dict(
        mode="unknown", style="night", ward_only=True,
        bbox=None,
        cam=(1555, 1344, 3000),
        target=(1555, 1344, 0),
        ortho=6900,
        sun=dict(elevation=55, azimuth=160, strength=1.6),
        fog=0.0,
        res=(1800, 1800),
    ),
}

# 見た目の型：地面の色、霧の色、空の強さ、区内の既知棟の色
STYLES = {
    "day": dict(ground="#bdb6a9", fog="#c9d3de", fog_strength=0.8, sky=0.12, exposure=-0.25, sun_color=(1.0, 0.96, 0.9)),
    "dusk": dict(ground="#b3a796", fog="#e3c9ae", fog_strength=0.75, sky=0.10, exposure=-0.2, sun_color=(1.0, 0.72, 0.45)),
    "night": dict(ground="#141a24", fog="#141a24", sky=0.0, world="#0d121b", sun_color=(0.78, 0.86, 1.0)),
}

# --- 色（リニア RGB） ---
def _srgb(h: str):
    h = h.lstrip("#")
    c = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c] + [1.0]


# 模型風（clay）の壁・屋根
C_WALL = _srgb("#e6e2da")
C_ROOF = _srgb("#f6f4ef")
C_CONTEXT = _srgb("#cfccc6")  # 区外の棟
# 不明群
C_UNKNOWN = _srgb("#f07a1a")
C_UNKNOWN_ROOF = _srgb("#ff9a3c")
# night 型の既知棟
C_NIGHT_WALL = _srgb("#5d6779")
C_NIGHT_ROOF = _srgb("#8c96a8")
# 高さの段階色（低→高、m）
HEIGHT_STOPS = [
    (0, "#ece7dc"),
    (8, "#e3d9c2"),
    (15, "#bfd0bb"),
    (30, "#7fb3a8"),
    (60, "#4a8aa6"),
    (120, "#35589a"),
    (200, "#4a2f86"),
    (260, "#7a2a7e"),
]


def _height_color(h: np.ndarray) -> np.ndarray:
    hs = np.array([s[0] for s in HEIGHT_STOPS], dtype=np.float32)
    cs = np.array([_srgb(s[1]) for s in HEIGHT_STOPS], dtype=np.float32)
    h = np.nan_to_num(h, nan=0.0)
    out = np.empty((len(h), 4), dtype=np.float32)
    for k in range(4):
        out[:, k] = np.interp(h, hs, cs[:, k])
    return out


# --- データ読み込みと切り出し ---
def load(path: str):
    d = np.load(path)
    data = {k: d[k] for k in d.files}
    face_len = data["face_len"]
    starts = np.zeros(len(face_len), dtype=np.int64)
    np.cumsum(face_len[:-1], out=starts[1:])
    data["face_start"] = starts
    data["in_ward"] = np.char.startswith(data["b_id"], "13104-")
    data["unknown"] = data["in_ward"] & (data["b_usage"] == UNKNOWN_USAGE)
    return data


def building_stats(data):
    """棟ごとの重心 xy と接地 z（最小 z）を面の頂点から求める。"""
    v = data["verts"]
    owner = np.repeat(data["face_owner"], data["face_len"])
    nb = len(data["b_id"])
    cnt = np.bincount(owner, minlength=nb).astype(np.float64)
    cx = np.bincount(owner, weights=v[:, 0], minlength=nb) / np.maximum(cnt, 1)
    cy = np.bincount(owner, weights=v[:, 1], minlength=nb) / np.maximum(cnt, 1)
    zmin = np.full(nb, np.inf)
    np.minimum.at(zmin, owner, v[:, 2])
    return cx, cy, zmin


def face_colors(data, mode: str, style: str):
    """面ごとの色（RGBA）と発光の強さを返す。"""
    nb = len(data["b_id"])
    in_ward, unknown = data["in_ward"], data["unknown"]
    owner = data["face_owner"]
    roof = data["face_kind"] == KIND_ROOF
    emit_b = np.zeros(nb, dtype=np.float32)

    if mode == "height":
        bcol = _height_color(data["b_height"])
        bcol[~in_ward] = C_CONTEXT
        col = bcol[owner].copy()
        col[roof, :3] = np.minimum(col[roof, :3] * 1.1, 1.0)
        return col, emit_b[owner]

    wall, rf = (C_NIGHT_WALL, C_NIGHT_ROOF) if style == "night" else (C_WALL, C_ROOF)
    bwall = np.tile(np.array(wall, dtype=np.float32), (nb, 1))
    broof = np.tile(np.array(rf, dtype=np.float32), (nb, 1))
    if mode == "unknown":
        if style != "night":
            bwall[~in_ward] = C_CONTEXT
            broof[~in_ward] = C_CONTEXT
        bwall[unknown] = C_UNKNOWN
        broof[unknown] = C_UNKNOWN_ROOF
        emit_b[unknown] = 1.0 if style == "night" else 0.15
    col = np.where(roof[:, None], broof[owner], bwall[owner])
    return col.astype(np.float32), emit_b[owner]


def build_mesh(name, verts, face_len, colors=None, emit=None):
    me = bpy.data.meshes.new(name)
    nv, nf = len(verts), len(face_len)
    me.vertices.add(nv)
    me.vertices.foreach_set("co", verts.astype(np.float32).ravel())
    me.loops.add(nv)
    me.loops.foreach_set("vertex_index", np.arange(nv, dtype=np.int32))
    me.polygons.add(nf)
    starts = np.zeros(nf, dtype=np.int32)
    np.cumsum(face_len[:-1], out=starts[1:])
    me.polygons.foreach_set("loop_start", starts)
    if colors is not None:
        attr = me.color_attributes.new("col", "FLOAT_COLOR", "FACE")
        attr.data.foreach_set("color", colors.astype(np.float32).ravel())
    if emit is not None:
        a = me.attributes.new("emit", "FLOAT", "FACE")
        a.data.foreach_set("value", emit.astype(np.float32))
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def select_faces(data, bsel: np.ndarray):
    fsel = bsel[data["face_owner"]]
    fsel &= data["face_kind"] != KIND_GROUND  # 接地面は地面と重なるので描かない
    fl = data["face_len"][fsel]
    st = data["face_start"][fsel]
    if not len(fl):
        return fsel, np.zeros((0, 3), np.float32), fl
    # 選んだ面の頂点番号を一括で並べる
    rep = np.repeat(st - np.concatenate([[0], np.cumsum(fl)[:-1]]), fl)
    idx = np.arange(int(fl.sum())) + rep
    return fsel, data["verts"][idx], fl


def build_ground(cx, cy, zmin, bsel, pad=600.0):
    """棟の接地高さから地形をつくる（建物の足元の標高を Delaunay で面にする）。"""
    ok = bsel & np.isfinite(zmin)
    pts = np.column_stack([cx, cy])[ok]
    zs = zmin[ok]
    zmed = float(np.median(zs))
    # 外周は中央値の高さで広げる（地平線まで地面を続ける）
    x0, y0 = pts.min(0) - pad
    x1, y1 = pts.max(0) + pad
    far = 60000.0
    ring = []
    for t in np.linspace(0, 1, 9)[:-1]:
        ring += [(x0 + (x1 - x0) * t, y0), (x1, y0 + (y1 - y0) * t),
                 (x1 - (x1 - x0) * t, y1), (x0, y1 - (y1 - y0) * t)]
    ring = np.array(ring)
    farring = np.array([(-far, -far), (far, -far), (far, far), (-far, far)])
    allp = np.vstack([pts, ring, farring])
    allz = np.concatenate([zs, np.full(len(ring) + len(farring), zmed)])
    try:
        from scipy.spatial import Delaunay
    except ImportError:  # Blender 同梱の Python には scipy が無い → 平らな地面で代用
        print("scipy が無いため、地面を接地標高の中央値の平面にする")
        allp, allz = farring, np.full(4, zmed)
        faces = [[0, 1, 2, 3]]
    else:
        faces = Delaunay(allp).simplices.tolist()
    verts = np.column_stack([allp, allz - 0.3]).astype(np.float32)
    me = bpy.data.meshes.new("ground")
    me.from_pydata(verts.tolist(), [], faces)
    me.update()
    ob = bpy.data.objects.new("ground", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob, zmed


# --- 材質・光・カメラ ---
def _material(name, fog_color, fog_density, color=None, rough=0.75, fog_strength=1.0):
    """拡散面＋距離霧（空気遠近）。color 省略時は面の属性 col を使い、属性 emit で発光させる。"""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes["Principled BSDF"]
    out = nodes["Material Output"]
    bsdf.inputs["Roughness"].default_value = rough
    if color is None:
        attr = nodes.new("ShaderNodeAttribute")
        attr.attribute_name = "col"
        links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        em = nodes.new("ShaderNodeAttribute")
        em.attribute_name = "emit"
        links.new(attr.outputs["Color"], bsdf.inputs["Emission Color"])
        mul = nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = 2.5
        links.new(em.outputs["Fac"], mul.inputs[0])
        links.new(mul.outputs[0], bsdf.inputs["Emission Strength"])
    else:
        bsdf.inputs["Base Color"].default_value = color
    shader = bsdf.outputs[0]
    if fog_density > 0:
        cam = nodes.new("ShaderNodeCameraData")
        # 霧の割合 = 1 - exp(-距離 × 密度)
        m1 = nodes.new("ShaderNodeMath"); m1.operation = "MULTIPLY"; m1.inputs[1].default_value = -fog_density
        m2 = nodes.new("ShaderNodeMath"); m2.operation = "EXPONENT"
        m3 = nodes.new("ShaderNodeMath"); m3.operation = "SUBTRACT"; m3.inputs[0].default_value = 1.0
        links.new(cam.outputs["View Distance"], m1.inputs[0])
        links.new(m1.outputs[0], m2.inputs[0])
        links.new(m2.outputs[0], m3.inputs[1])
        fog = nodes.new("ShaderNodeEmission")
        fog.inputs["Color"].default_value = fog_color
        fog.inputs["Strength"].default_value = fog_strength
        mix = nodes.new("ShaderNodeMixShader")
        links.new(m3.outputs[0], mix.inputs["Fac"])
        links.new(shader, mix.inputs[1])
        links.new(fog.outputs[0], mix.inputs[2])
        shader = mix.outputs[0]
    links.new(shader, out.inputs["Surface"])
    return m


def setup_world(sun_cfg, style):
    world = bpy.data.worlds.new("world")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    if "world" in style:
        bg.inputs["Color"].default_value = _srgb(style["world"])
        bg.inputs["Strength"].default_value = 1.0
    else:
        sky = nt.nodes.new("ShaderNodeTexSky")
        types = [i.identifier for i in sky.bl_rna.properties["sky_type"].enum_items]
        for t in ("MULTIPLE_SCATTERING", "NISHITA", "SINGLE_SCATTERING", "HOSEK_WILKIE"):
            if t in types:
                sky.sky_type = t
                break
        if hasattr(sky, "sun_elevation"):
            sky.sun_elevation = math.radians(sun_cfg["elevation"])
            sky.sun_rotation = math.radians(90 - sun_cfg["azimuth"])
        if hasattr(sky, "sun_disc"):
            sky.sun_disc = False
        nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = style["sky"]

    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = sun_cfg["strength"]
    sun.angle = math.radians(1.0)
    sun.color = style["sun_color"]
    ob = bpy.data.objects.new("sun", sun)
    bpy.context.scene.collection.objects.link(ob)
    # 方位角：北=0°時計回り → Blender（+Y=北、+X=東）での太陽の方向
    az, el = math.radians(sun_cfg["azimuth"]), math.radians(sun_cfg["elevation"])
    d = Vector((math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)))
    ob.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    if "world" in style:
        # 暗い地の型は、反対側から弱い補助光を当てて建物の輪郭を立てる
        fill = bpy.data.lights.new("fill", "SUN")
        fill.energy = sun_cfg["strength"] * 0.35
        fill.color = (0.55, 0.65, 0.9)
        fob = bpy.data.objects.new("fill", fill)
        bpy.context.scene.collection.objects.link(fob)
        fob.rotation_euler = Vector((-d.x, -d.y, 0.8)).to_track_quat("Z", "Y").to_euler()


def setup_camera(cfg, zbase):
    cam = bpy.data.cameras.new("cam")
    ob = bpy.data.objects.new("cam", cam)
    bpy.context.scene.collection.objects.link(ob)
    loc = Vector(cfg["cam"]) + Vector((0, 0, zbase))
    tgt = Vector(cfg["target"]) + Vector((0, 0, zbase))
    ob.location = loc
    up = "Y"
    ob.rotation_euler = (tgt - loc).to_track_quat("-Z", up).to_euler()
    if "ortho" in cfg:
        cam.type = "ORTHO"
        cam.ortho_scale = cfg["ortho"]
        ob.rotation_euler = (0, 0, 0)  # 真上から、上＝北
    else:
        cam.lens = cfg.get("lens", 35)
    cam.clip_start = 5
    cam.clip_end = 100000
    bpy.context.scene.camera = ob


def setup_render(cfg, samples, out):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 4
    sc.render.resolution_x, sc.render.resolution_y = cfg["res"]
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = out
    try:
        sc.view_settings.view_transform = "AgX"
        sc.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        pass


def render_shot(name, data, stats, samples, out_dir):
    cfg = SHOTS[name]
    style = STYLES[cfg["style"]]
    t0 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    cx, cy, zmin = stats
    if cfg["bbox"] is None:
        bsel = np.ones(len(cx), dtype=bool)
    else:
        x0, y0, x1, y1 = cfg["bbox"]
        bsel = (cx >= x0) & (cx <= x1) & (cy >= y0) & (cy <= y1)
    if cfg.get("ward_only"):
        bsel &= data["in_ward"]
    col, emit = face_colors(data, cfg["mode"], cfg["style"])
    fsel, verts, fl = select_faces(data, bsel)
    fog_c, fog_d = _srgb(style["fog"]), cfg.get("fog", 0.0)
    ob = build_mesh("buildings", verts, fl, col[fsel], emit[fsel])
    fs = style.get("fog_strength", 1.0)
    ob.data.materials.append(_material("bldg", fog_c, fog_d, fog_strength=fs))
    ground, zmed = build_ground(cx, cy, zmin, bsel)
    ground.data.materials.append(_material("ground", fog_c, fog_d, color=_srgb(style["ground"]), rough=0.95, fog_strength=fs))
    setup_world(cfg["sun"], style)
    setup_camera(cfg, zmed)
    out = os.path.join(out_dir, f"{name}.png")
    setup_render(cfg, samples, out)
    bpy.context.scene.view_settings.exposure = style.get("exposure", 0.0)
    n_ward = int((bsel & data["in_ward"]).sum())
    n_unk = int((bsel & data["unknown"]).sum())
    print(f"[{name}] 棟 {int(bsel.sum())}（新宿区 {n_ward}、不明群 {n_unk}）面 {int(fsel.sum())} 準備 {time.time()-t0:.1f}s")
    bpy.ops.render.render(write_still=True)
    print(f"[{name}] 書き出し {out}（{time.time()-t0:.1f}s）")
    return dict(shot=name, buildings=int(bsel.sum()), ward=n_ward, unknown=n_unk, seconds=round(time.time() - t0, 1))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", default="all", help="カット名（カンマ区切り）または all")
    ap.add_argument("--samples", type=int, default=128)
    ap.add_argument("--src", default="data/derived/render/meshes.npz")
    ap.add_argument("--out", default="data/derived/render")
    ap.add_argument("--scale", type=int, default=100, help="解像度の%%（下見は 40 など）")
    args = ap.parse_args(argv)

    shots = list(SHOTS) if args.shot == "all" else args.shot.split(",")
    data = load(args.src)
    stats = building_stats(data)
    os.makedirs(args.out, exist_ok=True)
    for s in shots:
        if args.scale != 100:
            SHOTS[s]["res"] = tuple(int(r * args.scale / 100) for r in SHOTS[s]["res"])
        render_shot(s, data, stats, args.samples, os.path.abspath(args.out))


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    main(argv)
