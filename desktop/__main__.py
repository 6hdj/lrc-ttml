"""桌面入口：``python -m desktop``。

真正的启动逻辑在 ``desktop/app.py``（那里写清楚了启动顺序为什么不能乱）。
"""
from __future__ import annotations

import sys

from desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
