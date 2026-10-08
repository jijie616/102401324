# -*- coding: utf-8 -*-
"""项目根目录入口脚本。

作用：让「clone 下来就能跑」，无需先 pip install 或设置 PYTHONPATH。

用法：
    python run.py                      # 全流程
    python run.py --limit 20           # 小样验证
    python run.py --stage crawl        # 只跑某个阶段
    python run.py --help               # 查看全部参数

等价于官方用法：PYTHONPATH=src python -m llm_danmaku.pipeline
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把 src 目录加入模块搜索路径
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from llm_danmaku.pipeline import main  # noqa: E402  (必须在 sys.path 调整之后导入)

if __name__ == "__main__":
    sys.exit(main())
