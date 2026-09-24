# -*- coding: utf-8 -*-
"""
生成一份**形态切换测试模组**（占位素材），用于验证「形态三档 + 技能位 + 变身过场」
在不改引擎源码的前提下是否可行。

规格来源：读 DyberPet v0.6.7 源码核出

* ``pet_conf.json`` 的 ``accessory_act`` → 自动登记成 ``acc_name``
  （``conf.py:167-182``），挂进右键的**动作菜单**
  （``Accessory.py:1326-1333``），门槛是 ``act_type[1] <= FV_lvl``
* 通用组件走 ``QAccessory``（``Accessory.py:155-162``），支持
  ``unique``（同名组件只存在一个）/ ``closable``（可关闭）/ ``follow_main``（跟随主体）
* 组件动画与主体动作**并行播放**（``register_accessory`` → ``setup_acc``），
  也就是说「变身过场」可以是一段独立的组件动画，不必占用主体动作槽
* ``day_night`` 证明「同一套素材按状态条件切换动画池」是引擎自带机制
  （``art_dev`` §昼夜系统：``to_night`` / ``night_random_act``）

用法::

    python -m dyberpet_modkit.gen_form_swap <输出目录> --name SwapCat

产物结构::

    SwapCat/
      pet_conf.json      # 三档 + accessory_act 技能位
      act_conf.json      # 主体动作 + 变身过场帧
      action/            # 占位帧
      info/info.json
"""
from __future__ import annotations

import argparse
import json
import math
import os

#: 画布边长
CANVAS = 128
#: 普通动作帧数
FRAMES = 4
#: 单帧时长（秒）
FRAME_REFRESH = 0.2

#: ⭐ 变身过场帧数 —— 设定端要求 6~10 帧，这里取 8
TRANSFORM_FRAMES = 8
#: 变身过场单帧时长 —— 短促、有节奏，总时长约 0.48s
TRANSFORM_REFRESH = 0.06

#: 三档形态。每档一种主色 + 一种外轮廓，模拟「同一只角色的不同形态」。
#: 用 shape 区分档位，用 color 区分档内状态，都是为了让肉眼一眼看出切没切过去。
FORMS = {
    # 2.5 档：主体（走路、被摸、跟随鼠标）
    "form25": {"color": (255, 154, 60, 255), "shape": "circle"},
    # 1.5 档：表情态（只在特定情绪弹出，不做全身）
    "form15": {"color": (232, 90, 130, 255), "shape": "square"},
    # 原型档：猫咪形态（相框卡片，极低频彩蛋）
    "formproto": {"color": (120, 200, 160, 255), "shape": "triangle"},
}

#: 三档的情绪脸（对应设定端定案的四张脸，这里用占位几何体代表）
EMOTION_FACES = {
    "angry_15": (232, 90, 90, 255),
    "hiss_15": (200, 120, 220, 255),
    "sneaky_15": (110, 170, 220, 255),
    "smug_15": (240, 190, 90, 255),
}


def _outer_mask(shape: str):
    from PIL import Image, ImageDraw

    mask = Image.new("L", (CANVAS, CANVAS), 0)
    draw = ImageDraw.Draw(mask)
    box = (20, 20, CANVAS - 20, CANVAS - 20)
    if shape == "circle":
        draw.ellipse(box, fill=255)
    elif shape == "square":
        draw.rounded_rectangle(box, radius=16, fill=255)
    elif shape == "triangle":
        draw.polygon([(CANVAS // 2, 20), (CANVAS - 20, CANVAS - 20), (20, CANVAS - 20)], fill=255)
    elif shape == "star":
        pts = []
        for i in range(10):
            r = 48 if i % 2 == 0 else 22
            a = -math.pi / 2 + math.pi * i / 5
            pts.append((CANVAS / 2 + r * math.cos(a), CANVAS / 2 + r * math.sin(a)))
        draw.polygon(pts, fill=255)
    return mask


def _blob(color, shape: str, index: int, frames: int, radius: float = 22.0):
    """外轮廓固定 + 内部高光点移动的一帧（保证帧间绝对像素一致）。"""
    from PIL import Image, ImageChops, ImageDraw

    mask = _outer_mask(shape)
    layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", (CANVAS, CANVAS), color), (0, 0), mask)

    angle = -math.pi / 2 + (2 * math.pi * index / max(frames, 1))
    cx = CANVAS / 2 + radius * math.cos(angle)
    cy = CANVAS / 2 + radius * math.sin(angle)
    ImageDraw.Draw(layer).ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(255, 255, 255, 230))

    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
    return layer


def _transform_frame(index: int, frames: int):
    """
    变身过场的一帧：小圆 → 胀大 → 收缩成方，全程带扩张光环。

    节奏（t = 0 → 1）：
    * 尺寸先快速胀大（t≈0.4 达峰，形成「蓄力」感），再收拢到目标档尺寸
    * 颜色在中点由源档色过渡到目标档色（配合尺寸收缩读作「变身」）
    * 光环由外向内绘制，避免被主体盖掉

    注意：**每一帧外轮廓都不同**，这正是过场的意义。
    官方「同一动作各帧绝对像素必须一致」针对循环动作，不适用于过场
    （本仓库 check.py 已按前缀豁免，但画布尺寸仍强制一致）。
    """
    from PIL import Image, ImageDraw

    t = index / max(frames - 1, 1)
    layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    src, dst = (255, 220, 140), (232, 90, 130)

    # ⚠️ 边界约束：光环与主体**同时**占画布，超界会被裁掉，
    #    客户看到的是「变身那一刻角色缺一角」。
    #    做法：光环用「逐帧真实余量」兜底（见下方 r + LINE_W > half 判断），
    #    主体尺寸本身按设计值走，不再按理论上限一刀切——那样会把动画压扁。
    LINE_W = 3
    half = CANVAS / 2

    # 主体尺寸：先胀后收（「蓄力 → 变身」）
    start_size, peak_size, end_size = 34.0, 52.0, 40.0
    if t <= 0.4:
        u = t / 0.4
        size = start_size + (peak_size - start_size) * math.sin(u * math.pi / 2)
    else:
        u = (t - 0.4) / 0.6
        size = peak_size + (end_size - peak_size) * (1 - math.cos(u * math.pi / 2))

    # 光环：蓄力段向外扩张，变身段快速收拢淡出（保证末帧总占地收敛）
    ring_fade = 1.0 if t <= 0.4 else max(0.0, 1.0 - (t - 0.4) / 0.45)
    for k in (3, 2, 1):
        grow = (6 * k + 26 * t) * ring_fade
        alpha = int(190 * (1 - t) / k)
        if alpha <= 2:
            continue
        r = size + grow
        if r + LINE_W > half:      # 兜底：任何一圈都不越界
            continue
        draw.ellipse(
            (half - r, half - r, half + r, half + r),
            outline=(255, 255, 255, alpha), width=LINE_W,
        )

    # 主体：圆 → 方，颜色同步过渡
    color = tuple(int(src[i] + (dst[i] - src[i]) * min(1.0, t / 0.55)) for i in range(3)) + (255,)
    box = (half - size, half - size, half + size, half + size)
    if t < 0.5:
        draw.ellipse(box, fill=color)
    else:
        draw.rounded_rectangle(box, radius=int(16 * min(1.0, (t - 0.5) / 0.5)), fill=color)

    return layer


def _check_frames_inside_canvas(module_dir: str) -> list:
    """
    检查每帧是否有内容**贴到画布边缘**（说明被裁切）。

    过场动画的峰值尺寸最容易撞上画布边界，撞上的后果是客户看到
    「变身那一刻角色缺一角」。这个检查把它变成可自动发现的问题。

    返回贴边的 ``(动作名, 帧文件名)`` 列表。
    """
    from PIL import Image

    action_dir = os.path.join(module_dir, "action")
    oversized = []
    for fp in sorted(os.listdir(action_dir)):
        if not fp.endswith(".png"):
            continue
        im = Image.open(os.path.join(action_dir, fp)).convert("RGBA")
        w, h = im.size
        a = im.getchannel("A")
        # 用直方图统计四条边的 alpha 之和（避免 Pillow 的 getdata 弃用警告）
        def edge_alpha(box):
            return sum(v * n for v, n in enumerate(a.crop(box).histogram()))

        edges = (
            edge_alpha((0, 0, w, 1)),
            edge_alpha((0, h - 1, w, h)),
            edge_alpha((0, 0, 1, h)),
            edge_alpha((w - 1, 0, w, h)),
        )
        if any(v > 0 for v in edges):
            oversized.append((fp, edges))
    return oversized


def generate(out_dir: str, name: str = "SwapCat") -> str:
    try:
        from PIL import Image
    except ImportError as exc:  # noqa: F841
        raise SystemExit("生成占位素材需要 Pillow：pip install pillow") from exc

    module_dir = os.path.join(os.path.abspath(out_dir), name)
    action_dir = os.path.join(module_dir, "action")
    info_dir = os.path.join(module_dir, "info")
    os.makedirs(action_dir, exist_ok=True)
    os.makedirs(info_dir, exist_ok=True)

    act_conf: dict = {}

    def emit(prefix: str, color, shape: str, frames: int, refresh: float):
        for i in range(frames):
            _blob(color, shape, i, frames).save(os.path.join(action_dir, f"{prefix}_{i}.png"))
        act_conf[prefix] = {"images": prefix, "act_num": 1, "frame_refresh": refresh}

    def emit_anim(prefix: str, frames: int, refresh: float):
        for i in range(frames):
            _transform_frame(i, frames).save(os.path.join(action_dir, f"{prefix}_{i}.png"))
        act_conf[prefix] = {"images": prefix, "act_num": 1, "frame_refresh": refresh}

    # --- 三档主体：每档一组循环动作（同一档内各帧尺寸一致）---
    for form, cfg in FORMS.items():
        emit(f"{form}_idle", cfg["color"], cfg["shape"], FRAMES, FRAME_REFRESH)
    # 拖拽 / 下落用 2.5 档的形态（真实项目里也是主体档承担这些）
    emit("drag", FORMS["form25"]["color"], FORMS["form25"]["shape"], FRAMES, FRAME_REFRESH)
    emit("fall", FORMS["form25"]["color"], FORMS["form25"]["shape"], FRAMES, FRAME_REFRESH)

    # --- 四张情绪脸（1.5 档表情态，只在特定情绪弹出）---
    for face, color in EMOTION_FACES.items():
        emit(face, color, "square", FRAMES, FRAME_REFRESH)

    # --- 变身过场（6~10 帧，这里 8 帧）---
    emit_anim("to_15", TRANSFORM_FRAMES, TRANSFORM_REFRESH)
    emit_anim("to_25", TRANSFORM_FRAMES, TRANSFORM_REFRESH)

    with open(os.path.join(module_dir, "act_conf.json"), "w", encoding="utf-8") as f:
        json.dump(act_conf, f, ensure_ascii=False, indent=2)

    pet_conf = {
        "width": CANVAS,
        "height": CANVAS,
        "scale": 1.0,
        "refresh": 5,
        "interact_speed": 0.02,
        "default": "form25_idle",
        "up": "form25_idle",
        "down": "form25_idle",
        "left": "form25_idle",
        "right": "form25_idle",
        "drag": "drag",
        "prefall": "fall",
        "fall": "fall",
        "on_floor": "form25_idle",
        "patpat": {
            "0": "form25_idle",
            "1": "form25_idle",
            "2": "form25_idle",
            "3": "form25_idle",
        },
        "random_act": [
            {"name": "stand", "act_list": ["form25_idle"], "act_prob": 1.0, "act_type": [2, 0]},
            # 表情态以极低概率随机弹出，模拟「特定情绪才出现」
            {"name": "闪一下生气脸", "act_list": ["angry_15"], "act_prob": 0.05, "act_type": [0, 0]},
        ],
        # ⭐ 技能位：形态切换走这里，门槛 act_type[1] = 好感度等级，0 = 一开始就能用
        "accessory_act": [
            {
                "name": "切换形态",
                "act_list": ["to_15", "form15_idle"],
                "acc_list": ["form15_idle"],
                "act_type": [0, 0],
                "follow_mouse": False,
                "above_main": True,
                "anchor": [0, 0],
            },
            {
                "name": "原型彩蛋",
                "act_list": ["formproto_idle"],
                "acc_list": ["formproto_idle"],
                "act_type": [0, 0],
                "follow_mouse": False,
                "above_main": True,
                "anchor": [0, 0],
            },
        ],
        "item_favorite": {},
        "item_dislike": {},
    }
    with open(os.path.join(module_dir, "pet_conf.json"), "w", encoding="utf-8") as f:
        json.dump(pet_conf, f, ensure_ascii=False, indent=2)

    Image.new("RGBA", (256, 256), FORMS["form25"]["color"]).save(os.path.join(info_dir, "pfp.png"))
    info = {
        "coverImages": [],
        "pfp": "pfp.png",
        "petName": name,
        "tages": {"formswap": "#FFD966"},
        "intro": "形态切换测试角色（占位几何素材），用于验证三档切换与技能位机制。",
        "author": {"name": "placeholder", "pfp": "pfp.png", "frameColor": "#FFD966", "links": {}, "infos": ""},
    }
    with open(os.path.join(info_dir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    return module_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dyberpet_modkit.gen_form_swap",
        description="生成形态切换测试模组（占位素材）",
    )
    parser.add_argument("out_dir", help="输出父目录（模组生成在其下的 <name>/ ）")
    parser.add_argument("--name", default="SwapCat", help="角色名，必须为英文（默认 SwapCat）")
    args = parser.parse_args(argv)

    module_dir = generate(args.out_dir, args.name)
    print(f"已生成形态切换测试模组: {module_dir}")
    print("放到 DyberPet 的 res/role/ 下，右键宠物 → 动作菜单 → 切换形态 / 原型彩蛋。")

    clipped = _check_frames_inside_canvas(module_dir)
    if clipped:
        print("\n⚠️ 以下帧贴到了画布边缘（可能被裁切）:")
        for fp, edges in clipped:
            print(f"    {fp}  四边 alpha 和 = {edges}")
        print("    → 调小 _transform_frame 里的 size_peak，或加大 CANVAS。")
    else:
        print("帧边界自检: OK —— 没有任何帧贴到画布边缘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
