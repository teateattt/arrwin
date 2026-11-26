import ctypes
import sys
from pathlib import Path

import win32api
import win32event


APP_TITLE = "Windows 窗口排列工具"
APP_USER_MODEL_ID = "ArrWin.WindowArranger"
INSTANCE_MUTEX_NAME = "Local\\ArrWin.WindowArranger"
ERROR_ALREADY_EXISTS = 183


def resource_path(*parts):
    """Return a resource path for both source runs and PyInstaller builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).resolve().parent
    return str(base_path.joinpath(*parts))


def set_windows_app_id(app_id=APP_USER_MODEL_ID):
    """Keep taskbar grouping and the executable icon under one app identity."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass


def show_single_instance_message():
    """Tell the user that the existing tray instance should be used."""
    message = "程序已经在运行，请从系统托盘打开主窗口。"
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "ArrWin",
            0x00000040,
        )
    except (AttributeError, OSError):
        print(message)


class SingleInstanceGuard:
    """Use a Windows named mutex to prevent duplicate tray icons."""

    def __init__(self, mutex_name=INSTANCE_MUTEX_NAME):
        self.mutex_name = mutex_name
        self._handle = None

    def acquire(self):
        if self._handle is not None:
            return True

        handle = win32event.CreateMutex(None, False, self.mutex_name)
        if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
            win32api.CloseHandle(handle)
            return False

        self._handle = handle
        return True

    def release(self):
        if self._handle is None:
            return
        try:
            win32api.CloseHandle(self._handle)
        finally:
            self._handle = None


class TrayController:
    """Manage the pystray icon without touching Tkinter from its thread."""

    def __init__(
        self,
        image_path,
        on_show,
        on_exit,
        title=APP_TITLE,
    ):
        self.image_path = image_path
        self.on_show = on_show
        self.on_exit = on_exit
        self.title = title
        self._icon = None
        self._started = False

    @property
    def is_running(self):
        return self._started and self._icon is not None

    def start(self):
        if self.is_running:
            return True

        icon = None
        try:
            import pystray
            from PIL import Image

            image = Image.open(self.image_path).convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem(
                    "打开主窗口",
                    self._handle_show,
                    default=True,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出程序", self._handle_exit),
            )
            icon = pystray.Icon(
                "arrwin",
                image,
                self.title,
                menu,
            )
            icon.run_detached()
        except Exception as exc:
            if icon is not None:
                try:
                    icon.stop()
                except Exception:
                    pass
            print("启动系统托盘失败: {0}".format(exc))
            return False

        self._icon = icon
        self._started = True
        return True

    def notify(self, message, title=None):
        if not self.is_running:
            return False
        try:
            self._icon.notify(message, title or self.title)
            return True
        except Exception as exc:
            print("显示托盘提示失败: {0}".format(exc))
            return False

    def stop(self):
        icon = self._icon
        self._started = False
        self._icon = None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception as exc:
            print("停止系统托盘失败: {0}".format(exc))

    def _handle_show(self, icon=None, item=None):
        try:
            self.on_show()
        except Exception as exc:
            print("处理托盘打开请求失败: {0}".format(exc))

    def _handle_exit(self, icon=None, item=None):
        try:
            self.on_exit()
        except Exception as exc:
            print("处理托盘退出请求失败: {0}".format(exc))
