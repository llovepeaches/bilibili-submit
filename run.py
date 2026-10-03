#!/usr/bin/env python3
"""免安装入口：python run.py <command>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bilibili_submit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
