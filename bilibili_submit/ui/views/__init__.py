"""各个页面。

页面只做两件事：把下层数据画出来，把用户操作翻译成下层调用。
业务逻辑一律不写在这里——否则 CLI 和 GUI 会各有一套实现，迟早走偏。

每个视图都实现 ``refresh()``，切换页面时由主窗口调用。
"""

from .history import HistoryView
from .login import LoginView
from .settings import SettingsView
from .tasks import TasksView
from .upload import UploadView

__all__ = [
    "HistoryView",
    "LoginView",
    "SettingsView",
    "TasksView",
    "UploadView",
]
