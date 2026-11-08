"""render/scene.py のカットを動画にする（カメラをゆっくり寄せ、途中で不明群を灯す）。

コマを PNG で書き出してから ffmpeg で MP4 にまとめる。コマは途中から再開できる
（書き出し済みのコマは飛ばす）。

使い方：
    python -m render.anim --anim kabukicho_reveal
    python -m render.anim --anim kabukicho_reveal --frames 1   # 1コマだけ試す
出力：data/derived/render/anim/<anim>/####.png と data/derived/render/<anim>.mp4（git 管理外）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from render import scene as S  # noqa: E402

# 動画の定義。カメラ・注視点は始点→終点を補間（Blender の既定のゆるい加減速）
ANIMS = {
    # 新宿駅東口〜歌舞伎町：白い街にカメラが寄り、途中で不明群が橙に灯る
    "kabukicho_reveal": dict(
        shot="kabukicho_unknown",
        seconds=5,
        fps=24,
        cam=((2080, -660, 430), (1700, -280, 330)),
        target=((1010, 390, 40), (930, 470, 40)),
        reveal=(1.0, 2.4),  # 灯し始め・灯し終わり（秒）
        res=(1280, 720),
        samples=8,  # デノイズ前提。白い模型なので低サンプルでも粒は消える
        bounces=2,
    ),
}


def _keyframe_reveal(mat, f0, f1):
    node = mat.node_tree.nodes["reveal"]
    sock = node.outputs[0]
    sock.default_value = 0.0
    sock.keyframe_insert("default_value", frame=f0)
    sock.default_value = 1.0
    sock.keyframe_insert("default_value", frame=f1)


def build(name, data, stats):
    a = ANIMS[name]
    cfg = dict(S.SHOTS[a["shot"]])
    style = S.STYLES[cfg["style"]]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    cx, cy, zmin = stats
    x0, y0, x1, y1 = cfg["bbox"]
    bsel = (cx >= x0) & (cx <= x1) & (cy >= y0) & (cy <= y1)

    col, emit = S.face_colors(data, "unknown", cfg["style"])
    col_base, _ = S.face_colors(data, "clay", cfg["style"])
    fsel, verts, fl = S.select_faces(data, bsel)
    ob = S.build_mesh("buildings", verts, fl, col[fsel], emit[fsel])
    attr = ob.data.color_attributes.new("col_base", "FLOAT_COLOR", "FACE")
    attr.data.foreach_set("color", col_base[fsel].astype(np.float32).ravel())

    fog_c, fog_d, fs = S._srgb(style["fog"]), cfg.get("fog", 0.0), style.get("fog_strength", 1.0)
    mat = S._material("bldg", fog_c, fog_d, fog_strength=fs, reveal=True)
    ob.data.materials.append(mat)
    ground, zmed = S.build_ground(cx, cy, zmin, bsel)
    ground.data.materials.append(S._material("ground", fog_c, fog_d, color=S._srgb(style["ground"]), rough=0.95, fog_strength=fs))
    S.setup_world(cfg["sun"], style)

    # カメラ：注視点（空のオブジェクト）を Track To で追う
    nframes = int(a["seconds"] * a["fps"])
    cam = bpy.data.cameras.new("cam")
    cam.lens = cfg.get("lens", 35)
    cam.clip_start, cam.clip_end = 5, 100000
    cob = bpy.data.objects.new("cam", cam)
    tgt = bpy.data.objects.new("target", None)
    sc.collection.objects.link(cob)
    sc.collection.objects.link(tgt)
    con = cob.constraints.new("TRACK_TO")
    con.target = tgt
    con.track_axis, con.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    z = Vector((0, 0, zmed))
    for frame, i in ((1, 0), (nframes, 1)):
        cob.location = Vector(a["cam"][i]) + z
        cob.keyframe_insert("location", frame=frame)
        tgt.location = Vector(a["target"][i]) + z
        tgt.keyframe_insert("location", frame=frame)
    sc.camera = cob
    r0, r1 = a["reveal"]
    _keyframe_reveal(mat, 1 + int(r0 * a["fps"]), 1 + int(r1 * a["fps"]))

    cfg["res"] = a["res"]
    S.setup_render(cfg, a["samples"], "")
    sc.view_settings.exposure = style.get("exposure", 0.0)
    sc.cycles.max_bounces = a.get("bounces", 4)
    sc.cycles.use_adaptive_sampling = False
    sc.render.fps = a["fps"]
    sc.frame_start, sc.frame_end = 1, nframes
    sc.render.use_persistent_data = True  # コマ間で BVH を使い回す
    sc.render.use_overwrite = False  # 書き出し済みのコマは飛ばす（再開用）
    sc.render.use_placeholder = True
    n_unk = int((bsel & data["unknown"]).sum())
    print(f"[{name}] 棟 {int(bsel.sum())}（不明群 {n_unk}）{nframes} コマ {a['res'][0]}x{a['res'][1]} {a['samples']}spp")
    return nframes


def encode(frames_dir, fps, out):
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", out,
    ]
    subprocess.run(cmd, check=True)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--anim", default="kabukicho_reveal", choices=list(ANIMS))
    ap.add_argument("--src", default="data/derived/render/meshes.npz")
    ap.add_argument("--out", default="data/derived/render")
    ap.add_argument("--frames", type=int, help="先頭から何コマだけ描くか（試し描き）")
    args = ap.parse_args(argv)

    data = S.load(args.src)
    stats = S.building_stats(data)
    t0 = time.time()
    nframes = build(args.anim, data, stats)
    frames_dir = os.path.abspath(os.path.join(args.out, "anim", args.anim))
    os.makedirs(frames_dir, exist_ok=True)
    sc = bpy.context.scene
    sc.render.filepath = os.path.join(frames_dir, "####")
    if args.frames:
        sc.frame_end = args.frames
    bpy.ops.render.render(animation=True)
    print(f"[{args.anim}] コマ書き出し完了（{time.time()-t0:.0f}s）")
    if sc.frame_end == nframes:
        out = os.path.abspath(os.path.join(args.out, f"{args.anim}.mp4"))
        encode(frames_dir, ANIMS[args.anim]["fps"], out)
        print(f"[{args.anim}] 動画 {out}")


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    main(argv)
