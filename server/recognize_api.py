#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图识别 API 包装：直接调用工作区「命盘截图识别」目录下的算法。

用法：python3 recognize_api.py img1.png [img2.png ...] → stdout JSON
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

RECOGNIZER_DIR = Path(os.environ.get(
    "MINGLUN_RECOGNIZER_DIR",
    str(Path(__file__).resolve().parent / "recognizer"),
))
sys.path.insert(0, str(RECOGNIZER_DIR))
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_IMAGE_DIMENSION = 8192


def validate_image(path: str) -> None:
    if Path(path).stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("图片文件过大")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if (image.format or "").upper() not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("图片格式不支持")
                width, height = image.size
                if width <= 0 or height <= 0 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    raise ValueError("图片尺寸不支持")
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片总像素过大")
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError("图片内容无效") from None


def main() -> None:
    from recognizer import recognize  # noqa: E402

    out = []
    for path in sys.argv[1:]:
        try:
            validate_image(path)
            cells = recognize(path)
            out.append({"file": Path(path).name, "cells": cells,
                        "error": None if cells is not None else "未找到「物品详情」面板"})
        except Exception as exc:  # 识别失败不中断整批
            print(f"recognize failed for {Path(path).name}: {type(exc).__name__}", file=sys.stderr)
            out.append({"file": Path(path).name, "cells": None, "error": "图片校验或识别失败"})
    print(json.dumps({"results": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
