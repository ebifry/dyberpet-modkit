# dyberpet-modkit

无需图形界面，在命令行校验 **DyberPet（呆啵宠物）** 角色模组是否合法。

## 为什么需要它

DyberPet 自带一个模组自检功能（`系统 → 自动添加`），它的核心是源码里的 `CheckCharFiles()`。
但 `DyberPet/conf.py` 顶层就 `from PySide6.QtGui import ...`，**没有图形环境根本无法 import**，
所以在 CI、服务器、或任何 headless 环境下都跑不了官方自检。

本工具用一组零依赖桩替换掉 PySide6，从而**直接调用 DyberPet 官方的 `CheckCharFiles()`**
（不是复刻、不是照文档猜），再叠加官方函数不做但官方文档明确要求的两条检查：

1. **`init_config` 的硬性必填字段** —— `pet_conf.json` 缺 `random_act` 会让源码直接 `KeyError` 崩溃；
2. **同一动作各帧的「绝对像素大小一致」** —— 官方文档要求，不一致时动画会抖。

## 安装

只需要 Python 3.9+。帧一致性检查需要 Pillow（可选，缺了会自动跳过）：

```bash
pip install pillow        # 可选
```

## 用法

```bash
# 校验一个模组目录（需要指向你本地的 DyberPet 源码，用于取官方校验函数）
python -m dyberpet_modkit.check ./我的模组目录 --repo /path/to/DyberPet

# 自检：自动生成一个占位模组，并跑正向 + 负向对照
python -m dyberpet_modkit.check --self-test --repo /path/to/DyberPet
```

输出示例：

```
模组目录: ./我的模组目录

[CheckCharFiles]  code=0 (Success)
[必填字段]        OK —— random_act 等硬性字段齐备
[帧尺寸一致性]    OK —— 所有动作各帧尺寸与非透明像素数一致

结论: ✅ 通过
```

退出码：`0` 通过；`1` 未通过（便于接进 CI）。

## 官方校验返回码

| code | 含义 |
|---|---|
| 0 | Success |
| 1 | `pet_conf.json` 缺失/损坏 |
| 2 | `act_conf.json` 缺失/损坏 |
| 3 | 某动作缺 `images` 属性 |
| 4 | 图片文件缺失 / 序号不连续 |
| 5 | `pet_conf.json` 缺 `default` / `drag` / `fall` |
| 6 | `pet_conf.json` 引用的动作在 `act_conf.json` 里不存在 |

## 生成占位模组

```bash
python -m dyberpet_modkit.gen_placeholder ./输出目录 --name TestCat
```

产出一个最小合法模组（`default` / `drag` / `fall` 三个动作 + `info/` 角色卡），
用于在拿到真实素材前打通「安装 → 导入 → 显示」链路。

## 模组最小结构

```
res/role/<英文角色名>/
├── pet_conf.json      ← 必填
├── act_conf.json      ← 必填（图片前缀 images 定义在这里，不在 pet_conf）
├── action/
│   └── <images>_0.png, _1.png, ...    ← 序号从 0 起，必须连续
└── info/info.json     ← 可选角色卡
```

⚠️ **文件名必须用英文** —— 官方文档明确指出：繁体中文字符路径解压后可能导致乱码与程序崩溃。

## 许可

本工具以 **GPL-3.0** 发布（与 DyberPet 本体一致）。

本仓库**不包含** DyberPet 的任何代码或素材；工具运行时从你本地已有的 DyberPet 副本导入其校验函数。
DyberPet 版权归其作者所有：<https://github.com/ChaozhongLiu/DyberPet>
