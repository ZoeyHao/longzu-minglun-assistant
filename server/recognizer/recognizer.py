#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《龙族：卡塞尔之门》精神命盘 · 背包截图识别器
==============================================

识别「物品详情」弹窗中的 4×5 格子：角色名（模板匹配）+ 数量（OCR）。

用法（作为模块引入）::

    from recognizer import recognize
    cells = recognize('IMG_6064.PNG')
    # cells: [{'row':1,'col':1,'name':'源稚生&源稚女','count':5,
    #          'name_confident':True,'count_confident':True,
    #          'mse':746.0,'gap':241.0,'cc':0.54}, ...]

用法（命令行）::

    python3 recognizer.py IMG_6064.PNG [更多图片...]

置信度规则（低置信=False，需人工确认）:
    名称: gap < 100 或 mse > 900
    数量: cc < 0.85 或提取失败(count=None)

依赖: Pillow, numpy。数据文件 tpl-data.json / digit-lib.json 必须与本脚本同目录。
"""
from PIL import Image
import numpy as np, json, base64, io, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 常量（与游戏截图几何绑定，原生分辨率 2796x1290 的半分辨率坐标系） ----
TW, TH = 48, 56                       # 模板尺寸（半分辨率）
PXH, PYH = 182.3 / 2, 153.5 / 2       # 网格列距 / 行距（半分辨率）
PANEL_DX, PANEL_DY = 60, 26           # 网格原点相对面板左上偏移（原生）
PANEL_W, PANEL_H = 946 / 2, 648 / 2   # 面板尺寸（半分辨率）
GRID_ROWS, GRID_COLS = 4, 5

# 置信度阈值
GAP_MIN, MSE_MAX = 100.0, 900.0       # 名称
CC_MIN = 0.85                          # 数量

# ---- 匹配权重掩码：脸部区加权 2.5，右下角数字区权重 0 ----
_my, _mx = np.mgrid[0:TH, 0:TW]
_BASE = ~((_mx > 31) & (_my > 41))
W_MASK = np.ones((TH, TW), np.float32)
_FACE = (_mx > 9) & (_mx < 41) & (_my > 6) & (_my < 49)
W_MASK[_FACE] = 2.5
W_MASK[~_BASE] = 0

_tpls = None
_samples = None
_DW = _DH = None


def _load():
    """惰性加载模板库与数字样本库。"""
    global _tpls, _samples, _DW, _DH
    if _tpls is not None:
        return
    data = json.load(open(os.path.join(_HERE, 'tpl-data.json')))
    _tpls = []
    for t in data['templates']:
        im = Image.open(io.BytesIO(base64.b64decode(t['d']))).convert('RGB')
        _tpls.append({'n': t['n'], 'r': t['r'], 'e': t['e'],
                      'img': np.asarray(im).astype(np.float32)})
    lib = json.load(open(os.path.join(_HERE, 'digit-lib.json')))
    _DW, _DH = lib['w'], lib['h']
    _samples = [(s['d'], np.array([int(ch) for ch in s['b']]).reshape(_DH, _DW) > 0)
                for s in lib['samples']]


# ================= 面板与网格定位 =================

def _find_panel(imh):
    """半分辨率图 → 面板边界 (x0, x1, y0, y1)。面板 = 亮灰低饱和区域。"""
    mx3 = imh.max(axis=2); mn3 = imh.min(axis=2)
    panel = (mn3 > 150) & ((mx3 - mn3) < 40)
    colsum = panel.sum(axis=0); rowsum = panel.sum(axis=1)
    H, W = imh.shape[:2]
    cols = np.where(colsum > H * 0.2)[0]; rows = np.where(rowsum > W * 0.12)[0]
    if len(cols) == 0 or len(rows) == 0:
        return None
    return cols.min(), cols.max(), rows.min(), rows.max()


def _grid_origin(imh):
    """面板左上 + 固定偏移 → 网格原点（半分辨率），带缩放系数。"""
    p = _find_panel(imh)
    if p is None:
        return None
    x0, x1, y0, y1 = p
    sx = (x1 - x0) / PANEL_W; sy = (y1 - y0) / PANEL_H
    return x0 + (PANEL_DX / 2) * sx, y0 + (PANEL_DY / 2) * sy, sx, sy


# ================= 角色名模板匹配（截尾加权 MSE） =================

def _trimmed_mse(cell, tpl, trim=0.15):
    d = ((cell - tpl) ** 2).mean(axis=2) * W_MASK
    dv = np.sort(d[W_MASK > 0].ravel())
    return dv[:int(len(dv) * (1 - trim))].mean()


def _match_cell(win, tpls):
    """半分辨率窗口 → {名字: (score, dy, dx)}，粗搜 step2 + 精修 ±2，同名取 min。"""
    H, Wd = win.shape[:2]
    best = {}
    for t in tpls:
        b = 1e18; bp = None
        for yy in range(0, H - TH + 1, 2):
            for xx in range(0, Wd - TW + 1, 2):
                s = _trimmed_mse(win[yy:yy + TH, xx:xx + TW], t['img'])
                if s < b:
                    b = s; bp = (yy, xx)
        by, bx = bp
        for yy in range(max(0, by - 2), min(H - TH, by + 2) + 1):
            for xx in range(max(0, bx - 2), min(Wd - TW, bx + 2) + 1):
                s = _trimmed_mse(win[yy:yy + TH, xx:xx + TW], t['img'])
                if s < b:
                    b = s; bp = (yy, xx)
        if t['n'] not in best or b < best[t['n']][0]:
            best[t['n']] = (b, bp[0], bp[1])
    return best


# ================= 数量 OCR（连通域 + 自锚定切片 + NCC 最近邻） =================

def _dilate2(m, it=2):
    r = m.copy()
    for _ in range(it):
        r2 = r.copy()
        r2[1:, :] |= r[:-1, :]; r2[:-1, :] |= r[1:, :]
        r2[:, 1:] |= r[:, :-1]; r2[:, :-1] |= r[:, 1:]
        r = r2
    return r


def _components(bin_):
    H, W = bin_.shape
    lbl = np.zeros((H, W), np.int32); n = 0
    for y0 in range(H):
        for x0 in range(W):
            if bin_[y0, x0] and lbl[y0, x0] == 0:
                n += 1; stack = [(y0, x0)]; lbl[y0, x0] = n
                while stack:
                    y, x = stack.pop()
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            yy, xx = y + dy, x + dx
                            if 0 <= yy < H and 0 <= xx < W and bin_[yy, xx] and lbl[yy, xx] == 0:
                                lbl[yy, xx] = n; stack.append((yy, xx))
    return lbl, n


def _norm_g(g):
    return np.asarray(Image.fromarray((g * 255).astype('uint8')).resize((_DW, _DH), Image.BILINEAR)) > 127


def _ncc(a, b):
    aa = a.astype(float).ravel(); bb = b.astype(float).ravel()
    aa -= aa.mean(); bb -= bb.mean()
    na = np.linalg.norm(aa); nb = np.linalg.norm(bb)
    if na < 1e-6 or nb < 1e-6:
        return -1
    return float((aa * bb).sum() / (na * nb))


def _classify_digit(g):
    best = None; bs = -2
    for d, v in _samples:
        sc = _ncc(g, v)
        if sc > bs:
            bs = sc; best = d
    return best, bs


def _ocr_count(imn, gx2, gy2):
    """imn 原生图; gx2,gy2 网格原点（原生坐标）。返回 (数量, 置信度cc)。"""
    reg = imn[gy2 + 55:gy2 + 152, gx2 + 30:gx2 + 145]
    white = reg.min(axis=2) > 170; dark = reg.max(axis=2) < 90
    dig = white & _dilate2(dark, 2)     # 白色数字且邻近深色描边
    lbl, n = _components(dig)
    comps = []
    for i in range(1, n + 1):
        ys, xs = np.where(lbl == i)
        h = ys.max() - ys.min() + 1
        if len(ys) < 40 or h < 12:
            continue
        comps.append([int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())])
    if not comps:
        return None, 0.0
    comps.sort()
    merged = []
    for cp in comps:
        if merged and cp[0] <= merged[-1][1]:   # x 重叠 → 同一数字的碎片合并
            merged[-1][1] = max(merged[-1][1], cp[1])
            merged[-1][2] = min(merged[-1][2], cp[2])
            merged[-1][3] = max(merged[-1][3], cp[3])
        else:
            merged.append(cp[:])
    main = max(merged, key=lambda m: m[3])
    y1 = main[3]                                   # 数字底
    x1 = max(m[1] for m in merged if abs(m[3] - y1) < 20)   # 右缘
    x0 = min(m[0] for m in merged if abs(m[3] - y1) < 20 and m[3] - m[2] >= 12)
    total_w = x1 - x0 + 1
    ndig = 1 if total_w <= 22 else (2 if total_w <= 40 else 3)
    out = ''
    conf = 1.0
    for k in range(ndig - 1, -1, -1):              # 17px 固定间距，从右往左切
        gx1 = x1 - k * 17; gx0 = max(x1 - k * 17 - 17, x0)
        g = dig[max(0, y1 - 29):y1 + 1, gx0:gx1 + 1]
        if g.sum() < 40:
            return None, 0.0
        d, sc = _classify_digit(_norm_g(g))
        out += d; conf = min(conf, sc)
    return int(out), conf


# ================= 主入口 =================

def recognize(image_path):
    """识别一张背包截图，返回格子列表（空格子自动跳过）。

    每个元素: {
        'row', 'col':     格子位置（1 起）
        'name':           角色名（58 个背包物品名之一）
        'count':          数量，提取失败为 None
        'name_confident': 名称是否高置信
        'count_confident': 数量是否高置信
        'mse', 'gap':     名称匹配分数 / 与次优差距（调试用）
        'cc':             数字 NCC 置信度（调试用）
    }
    面板未找到时返回 None。
    """
    _load()
    im = Image.open(image_path).convert('RGB')
    imh = np.asarray(im.resize((im.width // 2, im.height // 2), Image.BILINEAR)).astype(np.float32)
    imn = np.asarray(im).astype(int)
    g = _grid_origin(imh)
    if g is None:
        return None
    ox, oy, sx, sy = g
    cells = []
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            cx, cy = ox + c * PXH * sx, oy + r * PYH * sy
            x0i, y0i = int(cx), int(cy)
            cellreg = imh[y0i:y0i + TH, x0i:x0i + TW]
            if cellreg.shape[0] < TH or cellreg.shape[1] < TW:
                continue
            sat = cellreg.max(axis=2) - cellreg.min(axis=2)
            if (sat > 30).mean() < 0.05:          # 空格子
                continue
            m = 16                                 # 搜索窗外扩，容忍滚动偏移
            wy0, wx0 = max(0, y0i - m), max(0, x0i - m)
            win = imh[wy0:y0i + TH + m, wx0:x0i + TW + m]
            best = _match_cell(win, _tpls)
            ranking = sorted(best.items(), key=lambda kv: kv[1][0])
            name, (score, _by, _bx) = ranking[0]
            gap = ranking[1][1][0] - score if len(ranking) > 1 else 9999.0
            cnt, cconf = _ocr_count(imn, int(x0i * 2), int(y0i * 2))
            cells.append({
                'row': r + 1, 'col': c + 1,
                'name': name, 'count': cnt,
                'name_confident': bool(gap >= GAP_MIN and score <= MSE_MAX),
                'count_confident': bool(cnt is not None and cconf >= CC_MIN),
                'mse': round(float(score), 1), 'gap': round(float(gap), 1),
                'cc': round(float(cconf), 3),
            })
    return cells


if __name__ == '__main__':
    for fn in sys.argv[1:]:
        cells = recognize(fn)
        print(f'== {fn}: {"面板未找到" if cells is None else str(len(cells)) + " 格"}')
        for c in cells or []:
            flags = ('' if c['name_confident'] else ' [名称待确认]') + \
                    ('' if c['count_confident'] else ' [数量待确认]')
            print(f"  r{c['row']}c{c['col']}  {c['name']:<12} x{c['count']}{flags}")
