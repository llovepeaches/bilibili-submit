"""支持 ``python -m bilibili_submit`` 调用。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
