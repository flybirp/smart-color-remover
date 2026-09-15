#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能颜色抠图器 (Smart Color Remover)
====================================
输入一张图片 + 颜色描述（如 "红色"、"浅蓝"、"白色背景"、"#FF0000"），
将匹配的颜色区域变成透明，输出带 Alpha 通道的 PNG。

用法:
    python3 color_remover.py input.png --color "红色" [-o out.png] [选项]

示例:
    python3 color_remover.py photo.jpg --color "红色"
    python3 color_remover.py logo.png --color "白色" --background-only
    python3 color_remover.py icon.png --color "浅蓝" --tolerance 35 --feather 1
    python3 color_remover.py cat.png --color "#FFFFFF" --invert
"""

import argparse
import json
import os
import re
import sys

import numpy as np
from PIL import Image, ImageFilter

# ---------------------------------------------------------------------------
# 颜色词表
# ---------------------------------------------------------------------------

# 修饰词
MODIFIERS = {
    "light": {"浅", "淡", "浅色", "淡色", "亮色", "light", "pale", "浅淡"},
    "dark": {"深", "暗", "深色", "暗色", "dark", "deep"},
    "bright": {"亮", "鲜艳", "鲜", "明", "bright", "vivid", "saturated"},
}

# 基础色别名归一化（值 → 规范名）
COLOR_ALIASES = {
    "红": "红", "红色": "红", "赤": "红", "大红": "红", "red": "红",
    "橙": "橙", "橙色": "橙", "橘": "橙", "橘色": "橙", "桔": "橙", "桔色": "橙", "orange": "橙",
    "黄": "黄", "黄色": "黄", "yellow": "黄",
    "绿": "绿", "绿色": "绿", "草绿": "绿", "green": "绿",
    "青": "青", "青色": "青", "cyan": "青", "蓝绿": "青", "teal": "青",
    "蓝": "蓝", "蓝色": "蓝", "blue": "蓝",
    "紫": "紫", "紫色": "紫", "purple": "紫", "violet": "紫",
    "粉": "粉", "粉色": "粉", "粉红": "粉", "桃红": "粉", "pink": "粉",
    "品红": "品红", "洋红": "品红", "玫红": "品红", "玫瑰红": "品红", "magenta": "品红", "fuchsia": "品红",
    "棕": "棕", "棕色": "棕", "褐": "棕", "褐色": "棕", "咖啡": "棕", "咖啡色": "棕", "brown": "棕",
    "黑": "黑", "黑色": "黑", "black": "黑",
    "白": "白", "白色": "白", "white": "白",
    "灰": "灰", "灰色": "灰", "gray": "灰", "grey": "灰",
    "金": "金", "金色": "金", "gold": "金", "golden": "金",
    "银": "银", "银色": "银", "silver": "银",
    "米": "米", "米色": "米", "beige": "米",
}

# 有色相颜色的 HSV 匹配规则。hue 单位为度（0-360，可跨 0）。
# s/v 均为 PIL HSV 尺度 (0-255)。未列出的约束表示不限制。
HUE_RULES = {
    "红": dict(hue=(345, 15), s_min=35, v_min=25),
    "橙": dict(hue=(15, 40), s_min=40, v_min=150),  # 暗橙(V<150)归入棕色
    "黄": dict(hue=(40, 65), s_min=40, v_min=80),
    "绿": dict(hue=(65, 170), s_min=35, v_min=25),
    "青": dict(hue=(170, 200), s_min=35, v_min=25),
    "蓝": dict(hue=(200, 255), s_min=40, v_min=25),
    "紫": dict(hue=(255, 290), s_min=30, v_min=25),
    "品红": dict(hue=(290, 345), s_min=40, v_min=25),
    # 特殊色：在基础色相上加额外约束
    "粉": dict(hue=(325, 15), s_min=25, s_max=200, v_min=130),   # 浅红/浅品红系
    "棕": dict(hue=(15, 45), s_min=60, v_min=25, v_max=150),     # 暗橙即棕
    "金": dict(hue=(40, 58), s_min=90, v_min=120),               # 饱和亮黄
    "米": dict(hue=(30, 60), s_min=15, s_max=110, v_min=170),    # 极浅黄
}

# 无彩色（亮度主导）颜色的匹配规则
ACHROMATIC_RULES = {
    "黑": dict(v_max=45),
    "白": dict(v_min=225, s_max=55),
    "灰": dict(s_max=45, v_min=50, v_max=225),
    "银": dict(s_max=60, v_min=165),
}

DEG = 255.0 / 360.0  # 度 → PIL HSV H 尺度


# ---------------------------------------------------------------------------
# 颜色解析
# ---------------------------------------------------------------------------

def parse_hex(text):
    """解析 #RGB / #RRGGBB / #RRGGBBAA"""
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", text.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def parse_rgb(text):
    """解析 rgb(255,0,0) / (255,0,0) / 255,0,0 / 255 0 0"""
    t = text.strip().lower()
    t = re.sub(r"^rgb\s*", "", t).strip("() ")
    m = re.fullmatch(r"(\d{1,3})[\s,;]+(\d{1,3})[\s,;]+(\d{1,3})", t)
    if not m:
        return None
    rgb = tuple(int(x) for x in m.groups())
    if any(x > 255 for x in rgb):
        return None
    return rgb


def parse_color_word(text):
    """
    从文字中解析 (基础色名, 修饰词集合)。
    自动剔除 '扣掉/去掉/背景/透明' 等无关词。
    返回 (规范色名, mods set) 或 None。
    """
    t = text.strip().lower()
    # 去掉常见动词/名词干扰
    t = re.sub(r"(扣掉|抠掉|抠去|去掉|去除|移除|删除|消除|替换|换成|变成|变|设为|改为|"
               r"把|将|让|使|的|图片|图像|地方|区域|部分|背景|透明|颜色|色|color|remove|make|"
               r"turn|to|the|a|and|out|off)", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    mods = set()
    for key, words in MODIFIERS.items():
        for w in words:
            if w and re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t):
                mods.add(key)

    # 按长度降序尝试别名匹配（'浅红' 优先于 '红'）
    best = None
    for alias in sorted(COLOR_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", t):
            best = COLOR_ALIASES[alias]
            break
    return (best, mods) if best else None


def resolve_target(color_text):
    """
    解析颜色描述 → 匹配目标。
    返回 dict: {type: 'hue'|'achromatic'|'exact', ...}
    """
    t = color_text.strip()
    if not t:
        raise ValueError("颜色描述为空")

    # 1) 精确颜色值
    rgb = parse_hex(t) or parse_rgb(t)
    if rgb:
        return dict(type="exact", rgb=rgb, label=f"RGB{rgb}")

    # 2) 颜色名
    parsed = parse_color_word(t)
    if not parsed:
        order = ["红", "橙", "黄", "绿", "青", "蓝", "紫", "粉", "品红",
                 "棕", "黑", "白", "灰", "金", "银", "米"]
        supported = "、".join(sorted(set(COLOR_ALIASES.values()), key=order.index))
        raise ValueError(
            f"无法识别颜色 '{color_text}'。支持：{supported}（可加 浅/深/亮 修饰，"
            f"如 '浅蓝'、'深红'），或十六进制 '#FF0000'、'rgb(255,0,0)'。")

    name, mods = parsed
    if name in HUE_RULES:
        return dict(type="hue", name=name, mods=mods, rule=HUE_RULES[name], label="".join(sorted(mods)) + name)
    return dict(type="achromatic", name=name, mods=mods,
                rule=ACHROMATIC_RULES[name], label="".join(sorted(mods)) + name)


# ---------------------------------------------------------------------------
# 匹配算法
# ---------------------------------------------------------------------------

def apply_modifiers(rule, mods):
    """根据 浅/深/亮 修饰词调整 HSV 约束"""
    r = dict(rule)
    for mod in mods:
        if mod == "light":  # 浅：更亮且更不饱和（高明度 + 饱和度上限，排除标准色）
            r["v_min"] = max(r.get("v_min", 0), 140)
            r["s_min"] = max(8, r.get("s_min", 0) - 15)
            if r.get("s_max") is None:
                r["s_max"] = 190
        elif mod == "dark":  # 深：更暗、更饱和
            r["v_max"] = min(r.get("v_max", 255), 120)
            r["s_min"] = r.get("s_min", 0) + 20
        elif mod == "bright":  # 亮：高饱和
            r["s_min"] = r.get("s_min", 0) + 60
            r["v_min"] = max(r.get("v_min", 0), 100)
    return r


def apply_tolerance(rule, tol):
    """根据容差 (0-100) 放宽 HSV 约束"""
    r = dict(rule)
    if r.get("hue") is not None:
        expand = tol * 0.3  # 度（小系数：色相区间本身已宽，避免吃进相邻色系）
        lo, hi = r["hue"]
        r["hue"] = (lo - expand, hi + expand)
    if r.get("s_min") is not None:
        r["s_min"] = max(8, r["s_min"] - tol * 0.8)
    if r.get("s_max") is not None:
        r["s_max"] = min(255, r["s_max"] + tol * 0.8)
    if r.get("v_min") is not None:
        r["v_min"] = max(1, r["v_min"] - tol * 1.0)
    if r.get("v_max") is not None:
        r["v_max"] = min(255, r["v_max"] + tol * 1.0)
    return r


def hue_dist_h(h, lo, hi):
    """h (PIL 尺度 0-255) 是否落在 [lo, hi] 度区间（区间可跨 0，如 345°-15°）"""
    lo_p, hi_p = lo * DEG, hi * DEG
    if lo_p <= hi_p:
        return (h >= lo_p) & (h <= hi_p)
    # 跨 0 区间
    return (h >= lo_p) | (h <= hi_p)


def match_mask_hsv(hsv, target, tol):
    """在 HSV 数组上生成匹配掩码 (bool)"""
    h, s, v = hsv[..., 0].astype(np.int32), hsv[..., 1].astype(np.int32), hsv[..., 2].astype(np.int32)
    rule = apply_tolerance(apply_modifiers(target["rule"], target["mods"]), tol)
    mask = np.ones(h.shape, dtype=bool)
    if rule.get("hue") is not None:
        mask &= hue_dist_h(h, *rule["hue"])
    if rule.get("s_min") is not None:
        mask &= s >= rule["s_min"]
    if rule.get("s_max") is not None:
        mask &= s <= rule["s_max"]
    if rule.get("v_min") is not None:
        mask &= v >= rule["v_min"]
    if rule.get("v_max") is not None:
        mask &= v <= rule["v_max"]
    return mask


def match_mask_exact(rgb_arr, rgb_target, tol):
    """精确颜色值：感知加权 RGB 欧氏距离匹配"""
    radius = 50 + tol * 3.2
    t = np.array(rgb_target, dtype=np.float32)
    dr = rgb_arr[..., 0].astype(np.float32) - t[0]
    dg = rgb_arr[..., 1].astype(np.float32) - t[1]
    db = rgb_arr[..., 2].astype(np.float32) - t[2]
    dist = np.sqrt(2 * dr * dr + 4 * dg * dg + 3 * db * db)
    return dist <= radius


def keep_border_regions(mask):
    """只保留与图像边缘连通的匹配区域（用于抠除纯背景）"""
    try:
        from scipy import ndimage
    except ImportError:
        print("警告: 未安装 scipy，--background-only 功能不可用，已忽略该选项。", file=sys.stderr)
        return mask
    labels, n = ndimage.label(mask)
    if n == 0:
        return mask
    h, w = mask.shape
    border = np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]
    ]))
    border = border[border > 0]
    return np.isin(labels, border)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def process(input_path, color_text, output_path=None, tolerance=25,
            feather=0, invert=False, background_only=False, min_region=0):
    img = Image.open(input_path)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    rgb_arr = np.array(img)
    hsv = np.array(img.convert("HSV"))

    target = resolve_target(color_text)

    if target["type"] == "exact":
        mask = match_mask_exact(rgb_arr, target["rgb"], tolerance)
    else:
        mask = match_mask_hsv(hsv, target, tolerance)

    if background_only:
        mask = keep_border_regions(mask)

    if min_region > 0:
        mask = remove_small_regions(mask, min_region)

    # 生成 alpha：匹配目标颜色的区域 → 透明(0)，其余 → 不透明(255)
    # --invert 反转：只保留匹配颜色，其余透明
    opaque = mask if invert else ~mask
    alpha = (opaque * 255).astype(np.uint8)
    if feather > 0:
        a_img = Image.fromarray(alpha, mode="L").filter(
            ImageFilter.GaussianBlur(radius=feather))
        alpha = np.array(a_img)

    out = rgb_arr.copy()
    out[..., 3] = alpha

    if output_path is None:
        root, _ = os.path.splitext(input_path)
        output_path = root + "_transparent.png"
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    Image.fromarray(out, mode="RGBA").save(output_path, format="PNG")

    total = mask.size
    removed = int(np.count_nonzero(alpha == 0))
    stats = {
        "input": input_path,
        "output": output_path,
        "size": f"{img.width}x{img.height}",
        "target": target["label"],
        "tolerance": tolerance,
        "feather": feather,
        "invert": invert,
        "background_only": background_only,
        "removed_pixels": removed,
        "removed_percent": round(removed / total * 100, 1),
    }
    return stats


def remove_small_regions(mask, min_pixels):
    """移除面积小于 min_pixels 的孤立匹配区域（去噪点）"""
    try:
        from scipy import ndimage
    except ImportError:
        return mask
    labels, n = ndimage.label(mask)
    if n == 0:
        return mask
    counts = np.bincount(labels.ravel())
    keep = np.zeros_like(counts, dtype=bool)
    keep[1:] = counts[1:] >= min_pixels
    return keep[labels]


def main():
    ap = argparse.ArgumentParser(
        description="智能颜色抠图器：将指定颜色变成透明", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="输入图片路径")
    ap.add_argument("-c", "--color", required=True,
                    help="要抠掉的颜色：中文/英文名（红、浅蓝、白色…）或 #FF0000 / rgb(255,0,0)")
    ap.add_argument("-o", "--output", default=None, help="输出 PNG 路径（默认 原名_transparent.png）")
    ap.add_argument("-t", "--tolerance", type=float, default=25,
                    help="容差 0-100，越大抠得越宽（默认 25）")
    ap.add_argument("-f", "--feather", type=float, default=0,
                    help="边缘羽化半径 0-5，平滑抠图边缘（默认 0）")
    ap.add_argument("--invert", action="store_true",
                    help="反选：保留该颜色，其余变透明")
    ap.add_argument("--background-only", action="store_true",
                    help="只抠与图像边缘连通的区域（适合抠除纯色背景，保护物体内部同色区域）")
    ap.add_argument("--min-region", type=int, default=0,
                    help="移除小于 N 像素的孤立噪点区域（默认 0 不移除）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"错误: 找不到输入图片 '{args.input}'", file=sys.stderr)
        sys.exit(1)

    try:
        stats = process(args.input, args.color, args.output,
                        tolerance=args.tolerance, feather=args.feather,
                        invert=args.invert, background_only=args.background_only,
                        min_region=args.min_region)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False))
    else:
        print(f"✅ 抠图完成")
        print(f"   输入: {stats['input']} ({stats['size']})")
        print(f"   目标颜色: {stats['target']} (容差 {stats['tolerance']})")
        print(f"   抠掉像素: {stats['removed_pixels']} ({stats['removed_percent']}%)")
        print(f"   输出: {stats['output']}")


if __name__ == "__main__":
    main()
