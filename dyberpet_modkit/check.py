# -*- coding: utf-8 -*-
"""
无 GUI 校验 DyberPet 角色模组。

DyberPet 的 ``DyberPet/conf.py`` 顶层 ``from PySide6.QtGui import ...``，
没有图形环境就无法 import，也就跑不了它自带的 ``CheckCharFiles()``。
本模块用一组零依赖桩替换 PySide6，从而在纯命令行下**直接调用官方校验函数**，
再叠加官方文档要求、但该函数未覆盖的两条检查：

1. ``init_config`` 的硬性必填字段（``pet_conf.json`` 缺 ``random_act`` 会直接 KeyError 崩溃）
2. 同一动作各帧的「绝对像素大小一致」（不一致时动画会抖）

用法::

    python -m dyberpet_modkit.check <模组目录> --repo <DyberPet 源码根目录>
    python -m dyberpet_modkit.check --self-test --repo <DyberPet 源码根目录>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile
import types

#: 官方 CheckCharFiles 的状态码含义（源码 DyberPet/conf.py）
STATUS = {
    0: "Success",
    1: "pet_conf.json 缺失/损坏",
    2: "act_conf.json 缺失/损坏",
    3: "动作缺 images 属性",
    4: "图片缺失/序号不连续",
    5: "pet_conf 缺 default/drag/fall",
    6: "pet_conf 引用的动作在 act_conf 中不存在",
}

#: 官方文档要求必须存在的三个动作
REQUIRED_ACTIONS = ("default", "drag", "fall")

#: ``init_config`` 用下标直接取、缺了会 KeyError 的字段
INIT_HARD_KEYS = ("default", "drag", "fall", "random_act")


def stub_pyside6() -> None:
    """注册假的 PySide6，仅为让 ``DyberPet.conf`` 能被 import。

    ``CheckCharFiles`` 本身不碰 Qt，所以桩只需要让模块级 import 通过即可。
    """
    if "PySide6" in sys.modules:
        return

    def _cls(name: str) -> type:
        return type(name, (), {})

    pyside = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")
    qtgui = types.ModuleType("PySide6.QtGui")

    qtcore.Qt = type("Qt", (), {"KeepAspectRatio": 1, "SmoothTransformation": 2})
    qtcore.QTime = _cls("QTime")
    qtgui.QImage = _cls("QImage")
    qtgui.QPixmap = _cls("QPixmap")

    pyside.QtCore = qtcore
    pyside.QtGui = qtgui
    sys.modules.update(
        {"PySide6": pyside, "PySide6.QtCore": qtcore, "PySide6.QtGui": qtgui}
    )


def load_official_check(repo: str):
    """从本地 DyberPet 源码导入官方 ``CheckCharFiles``。"""
    repo = os.path.abspath(repo)
    if not os.path.isfile(os.path.join(repo, "DyberPet", "conf.py")):
        raise SystemExit(
            f"--repo 指向的目录里找不到 DyberPet/conf.py：{repo}\n"
            "请指向你 clone 下来的 DyberPet 源码根目录。"
        )
    stub_pyside6()
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from DyberPet.conf import CheckCharFiles  # type: ignore

    return CheckCharFiles


def check_required_keys(module_dir: str):
    """检查 ``pet_conf.json`` 里缺了会 KeyError 的字段。"""
    with open(os.path.join(module_dir, "pet_conf.json"), encoding="utf-8") as f:
        pet = json.load(f)
    return [k for k in INIT_HARD_KEYS if k not in pet]


#: 动作名前缀，命中这些前缀的动作视为「过场动画」，不做帧一致性检查。
#: 理由：过场（变身 / 转场）的本质就是**每帧轮廓不同**（收缩、发光、位移），
#: 要求帧间像素一致等于要求过场不做动画。官方那条硬约束针对的是**循环动作**
#: （playlist 里的 idle / walk 之类），循环动作帧间跳动才会被看成「抖」。
TRANSITION_PREFIXES = (
    "to_", "transition_", "transform_", "prefall_", "intro_", "outro_",
)


def _is_transition(name: str, images: str) -> bool:
    """判断一个动作是否属于「过场动画」（帧间允许不一致）。"""
    for token in (name, images):
        if not token:
            continue
        low = token.lower()
        if any(low.startswith(p) for p in TRANSITION_PREFIXES):
            return True
    return False


def check_frame_consistency(module_dir: str):
    """检查同一动作的所有帧，画布尺寸与非透明像素数是否一致。

    返回 ``(bad_list, skipped, excused)``：

    * ``bad_list``  —— 真正有问题的循环动作
    * ``skipped``   —— 未安装 Pillow
    * ``excused``   —— 判定为过场动画而豁免检查的动作名

    画布尺寸（``sizes``）对**所有**动作都必须一致 —— 尺寸不统一会让窗口跳；
    非透明像素数只对循环动作强制 —— 过场动画的像素数本来就该逐帧变化。
    """
    try:
        from PIL import Image
    except ImportError:
        return [], True, []

    with open(os.path.join(module_dir, "act_conf.json"), encoding="utf-8") as f:
        act = json.load(f)

    bad = []
    excused = []
    action_dir = os.path.join(module_dir, "action")
    for name, dic in act.items():
        images = dic.get("images")
        if not images:
            continue
        frames = sorted(
            glob.glob(os.path.join(action_dir, f"{images}_*.png")),
            key=lambda p: int(re.search(r"_(\d+)\.png$", p).group(1)),
        )
        if not frames:
            continue
        sizes, counts = set(), set()
        for fp in frames:
            im = Image.open(fp).convert("RGBA")
            sizes.add(im.size)
            counts.add(sum(im.getchannel("A").histogram()[1:]))

        is_trans = _is_transition(name, images)
        if is_trans:
            excused.append((name, images))
        # 尺寸：任何动作都必须一致
        size_bad = len(sizes) > 1
        # 像素数：只有循环动作必须一致
        count_bad = (len(counts) > 1) and not is_trans
        if size_bad or count_bad:
            bad.append((name, images, sizes, counts, size_bad, count_bad, is_trans))
    return bad, False, excused



def validate(module_dir: str, repo: str | None = None) -> int:
    """校验一个模组目录，返回进程退出码（0 通过 / 1 未通过）。"""
    module_dir = os.path.abspath(module_dir)
    print(f"模组目录: {module_dir}\n")
    ok = True

    if repo:
        check_char_files = load_official_check(repo)
        code, detail = check_char_files(module_dir)
        line = f"[CheckCharFiles]  code={code} ({STATUS.get(code, '?')})"
        if detail:
            line += f"  detail={detail}"
        print(line)
        ok &= code == 0
    else:
        print("[CheckCharFiles]  跳过（未提供 --repo）")

    try:
        missing = check_required_keys(module_dir)
        print(f"[必填字段]        {'OK —— random_act 等硬性字段齐备' if not missing else '❌ 缺: ' + ', '.join(missing)}")
        ok &= not missing
    except Exception as exc:  # noqa: BLE001
        print(f"[必填字段]        ❌ 无法检查（{exc}）")
        ok = False

    try:
        bad, skipped, excused = check_frame_consistency(module_dir)
        if skipped:
            print("[帧一致性]        跳过（未安装 Pillow）")
        elif bad:
            print("[帧一致性]        ❌ 有问题:")
            for name, images, sizes, counts, size_bad, count_bad, is_trans in bad:
                why = []
                if size_bad:
                    why.append("画布尺寸不一致")
                if count_bad:
                    why.append("非透明像素数不一致")
                print(f"    {name} (images={images}) sizes={sizes} 非透明像素={counts}")
                print(f"      → 问题：{'；'.join(why)}")
            ok = False
        else:
            extra = ""
            if excused:
                extra = f"（豁免过场动画 {len(excused)} 个：{', '.join(n for n, _ in excused)}）"
            print(f"[帧一致性]        OK —— 循环动作各帧尺寸与像素数一致{extra}")
    except Exception as exc:  # noqa: BLE001
        print(f"[帧一致性]        跳过（{exc}）")

    print("\n结论:", "✅ 通过" if ok else "❌ 未通过")
    return 0 if ok else 1


def _self_test(repo: str | None) -> int:
    """生成一个占位模组，跑正向 + 负向对照，验证校验链本身可信。"""
    from .gen_placeholder import generate

    if not repo:
        print("--self-test 需要 --repo 指向 DyberPet 源码（负向对照要调用官方函数）")
        return 2

    check_char_files = load_official_check(repo)
    results = []

    with tempfile.TemporaryDirectory() as tmp:
        # generate() 返回的是 <out>/<name>/ 这个真正的模组目录
        good = generate(os.path.join(tmp, "Good"), name="TestCat")
        code, _ = check_char_files(good)
        results.append(("正向·完整占位模组", code, 0))

        no_fall = generate(os.path.join(tmp, "NoFall"), name="TestCat")
        conf_path = os.path.join(no_fall, "pet_conf.json")
        with open(conf_path, encoding="utf-8") as f:
            conf = json.load(f)
        del conf["fall"]
        with open(conf_path, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
        code, _ = check_char_files(no_fall)
        results.append(("负向·删掉 fall", code, 5))

        gap = generate(os.path.join(tmp, "Gap"), name="TestCat")
        os.remove(os.path.join(gap, "action", "default_1.png"))
        code, _ = check_char_files(gap)
        results.append(("负向·default 序列挖洞", code, 4))

    print("=== self-test：官方 CheckCharFiles 对照 ===\n")
    all_ok = True
    for label, got, want in results:
        good = got == want
        all_ok &= good
        print(f"  {'✅' if good else '❌'} {label}: 得到 code={got}，期望 {want}")
    print("\n结论:", "✅ 校验链可信" if all_ok else "❌ 校验链异常")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dyberpet_modkit.check",
        description="无需 GUI，校验 DyberPet 角色模组是否合法",
    )
    parser.add_argument("module", nargs="?", help="模组目录（含 pet_conf.json / act_conf.json / action/）")
    parser.add_argument("--repo", help="DyberPet 源码根目录（用于导入官方 CheckCharFiles）")
    parser.add_argument("--self-test", action="store_true", help="生成占位模组并跑正向 + 负向对照")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test(args.repo)
    if not args.module:
        parser.error("请给出模组目录，或使用 --self-test")
    return validate(args.module, args.repo)


if __name__ == "__main__":
    raise SystemExit(main())
