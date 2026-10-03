#!/usr/bin/env python3
"""PyInstaller 打包入口。

与 run.py 的区别：这里不做 sys.path 操作，专供打包使用（打包后
__file__ 指向解包临时目录，sys.path 注入没有意义）。
"""

import sys

from bilibili_submit.cli import main

if __name__ == "__main__":
    sys.exit(main())
