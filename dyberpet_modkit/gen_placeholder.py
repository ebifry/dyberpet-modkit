# -*- coding: utf-8 -*-
"""
生成一份**最小的合法** DyberPet 角色模组（占位素材）。

用于在拿到真实素材之前，先把「安装 → 导入 → 显示」整条链路跑通。
严格按 DyberPet 官方文档与 v0.6.7 源码的 schema 产出：

* ``pet_conf.json``  —— 含必填的 ``default`` / ``drag`` / ``fall`` 与 ``random_act``
* ``act_conf.json``  —— 每个动作的 ``images`` 前缀与单帧时长
* ``action/<images>_<i>.png`` —— 序号从 0 起、连续
* ``info/info.json`` —— 可选角色卡

设计要点：每个动作的外轮廓在**所有帧里完全一致**，只有内部一个高光点在动，
从而满足官方「同一动作各帧角色绝对像素大小必须相同」的硬约束。

用法::

    python -m dyberpet_modkit.gen_placeholder <输出目录> --name TestCat
"""
from __future__ import annotations

import argparse
import json
import math
import os

#: 画布边长（官方默认 128；图片超出 128 时须在 pet_conf.json 显式写 width/height）
CANVAS = 128
#: 每个动作的帧数
FRAMES = 4
#: 单帧时长（秒）—— 官方建议所有动作保持一致
FRAME_REFRESH = 0.2

#: 三个 P0 动作：各自一种颜色与一种外轮廓
ACTIONS = {
    "default": {"color": (255, 154, 60, 255), "shape": "circle"},
    "drag": {"color": (74, 144, 217, 255), "shape": "square"},
    "fall": {"color": (155, 89, 182, 255), "shape": "triangle"},
}


def _outer_mask(shape: str):
    """返回所有帧共享的外轮廓遮罩（保证绝对像素一致）。"""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (CANVAS, CANVAS), 0)
    draw = ImageDraw.Draw(mask)
    box = (20, 20, CANVAS - 20, CANVAS - 20)  # 88x88
    if shape == "circle":
        draw.ellipse(box, fill=255)
    elif shape == "square":
        draw.rounded_rectangle(box, radius=16, fill=255)
    elif shape == "triangle":
        draw.polygon([(CANVAS // 2, 20), (CANVAS - 20, CANVAS - 20), (20, CANVAS - 20)], fill=255)
    return mask


def _frame(color, shape: str, index: int):
    """画一帧：外轮廓固定，只有内部高光点沿弧线移动。"""
    from PIL import Image, ImageChops, ImageDraw

    mask = _outer_mask(shape)
    layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", (CANVAS, CANVAS), color), (0, 0), mask)

    angle = -math.pi / 2 + (2 * math.pi * index / FRAMES)
    cx = CANVAS / 2 + 22 * math.cos(angle)
    cy = CANVAS / 2 + 22 * math.sin(angle)
    ImageDraw.Draw(layer).ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(255, 255, 255, 230))

    # 关键：整帧 alpha 重新乘以外轮廓遮罩，任何元素都裁在轮廓内，
    # 保证每一帧的非透明像素足迹完全一致（否则三角形窄边会漏出高光点）。
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
    return layer


def generate(out_dir: str, name: str = "TestCat") -> str:
    """在 ``out_dir``/<name>/ 生成占位模组，返回该模组目录。"""
    try:
        from PIL import Image
    except ImportError as exc:  # noqa: F841
        raise SystemExit("生成占位素材需要 Pillow：pip install pillow") from exc

    module_dir = os.path.join(os.path.abspath(out_dir), name)
    action_dir = os.path.join(module_dir, "action")
    info_dir = os.path.join(module_dir, "info")
    os.makedirs(action_dir, exist_ok=True)
    os.makedirs(info_dir, exist_ok=True)

    act_conf = {}
    for act, cfg in ACTIONS.items():
        for i in range(FRAMES):
            _frame(cfg["color"], cfg["shape"], i).save(os.path.join(action_dir, f"{act}_{i}.png"))
        act_conf[act] = {"images": act, "act_num": 1, "frame_refresh": FRAME_REFRESH}

    with open(os.path.join(module_dir, "act_conf.json"), "w", encoding="utf-8") as f:
        json.dump(act_conf, f, ensure_ascii=False, indent=2)

    pet_conf = {
        "width": CANVAS,
        "height": CANVAS,
        "scale": 1.0,
        "refresh": 5,
        "interact_speed": 0.02,
        "default": "default",
        "up": "default",
        "down": "default",
        "left": "default",
        "right": "default",
        "drag": "drag",
        "prefall": "fall",
        "fall": "fall",
        "on_floor": "default",
        "patpat": "default",
        "random_act": [
            {"name": "stand", "act_list": ["default"], "act_prob": 1.0, "act_type": [2, 0]}
        ],
        "item_favorite": {},
        "item_dislike": {},
    }
    with open(os.path.join(module_dir, "pet_conf.json"), "w", encoding="utf-8") as f:
        json.dump(pet_conf, f, ensure_ascii=False, indent=2)

    Image.new("RGBA", (256, 256), ACTIONS["default"]["color"]).save(os.path.join(info_dir, "pfp.png"))
    info = {
        "coverImages": [],
        "pfp": "pfp.png",
        "petName": name,
        "tages": {"placeholder": "#C5E0B4"},
        "intro": "占位测试角色，仅用于验证 DyberPet 模组导入流程，不是真实素材。",
        "author": {"name": "placeholder", "pfp": "pfp.png", "frameColor": "#8FAADC", "links": {}, "infos": ""},
    }
    with open(os.path.join(info_dir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    return module_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dyberpet_modkit.gen_placeholder",
        description="生成一份最小合法的 DyberPet 占位模组",
    )
    parser.add_argument("out_dir", help="输出父目录（模组会生成在其下的 <name>/ ）")
    parser.add_argument("--name", default="TestCat", help="角色名，必须为英文（默认 TestCat）")
    args = parser.parse_args(argv)

    module_dir = generate(args.out_dir, args.name)
    print(f"已生成占位模组: {module_dir}")
    print("把它整个文件夹放到 DyberPet 的 res/role/ 下即可被识别。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
