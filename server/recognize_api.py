#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图识别 API 包装：直接调用工作区「命盘截图识别」目录下的算法。

用法：python3 recognize_api.py img1.png [img2.png ...] → stdout JSON
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RECOGNIZER_DIR = Path(os.environ.get(
    "MINGLUN_RECOGNIZER_DIR",
    str(Path(__file__).resolve().parent / "recognizer"),
))
sys.path.insert(0, str(RECOGNIZER_DIR))


def main() -> None:
    from recognizer import recognize  # noqa: E402

    out = []
    for path in sys.argv[1:]:
        try:
            cells = recognize(path)
            out.append({"file": Path(path).name, "cells": cells,
                        "error": None if cells is not None else "未找到「物品详情」面板"})
        except Exception as exc:  # 识别失败不中断整批
            out.append({"file": Path(path).name, "cells": None, "error": str(exc)})
    print(json.dumps({"results": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
