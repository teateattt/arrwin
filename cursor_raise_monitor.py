"""Raise the regular window under the cursor after Ctrl is pressed and moved."""

import threading
import time

import win32api
import win32con
import win32gui

from window_occlusion_monitor import (
    MonitorEvent,
    NON_APPLICATION_WINDOW_CLASSES,
    enable_thread_dpi_awareness,
    format_monitor_name,
    get_monitor_id_for_window,
    get_window_title,
    get_suspending_windows,
    is_current_process_window,
    raise_regular_window,
)


DEFAULT_POLL_INTERVAL_SECONDS = 0.02
DEFAULT_MOVEMENT_THRESHOLD_PIXELS = 8
DEFAULT_SUSPENSION_CHECK_INTERVAL_SECONDS = 0.5


def is_regular_hover_window(
    hwnd,
    current_pid=None,
    exclude_own_window=True,
):
    """Return whether hwnd is a regular application window that may be raised."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
        return False

    try:
        class_name = win32gui.GetClassName(hwnd)
        if class_name in NON_APPLICATION_WINDOW_CLASSES:
            return False

        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex_style & (
            win32con.WS_EX_TOPMOST
            | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE
        ):
            return False

        owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
        if owner:
            return False

        if exclude_own_window and is_current_process_window(
            hwnd,
            current_pid,
        ):
            return False
    except Exception:
        return False

    return True


def get_window_under_cursor(exclude_own_window=True):
    """Return the regular root window under the cursor, or None."""
    try:
        x, y = win32api.GetCursorPos()
        hwnd = win32gui.WindowFromPoint((x, y))
        if not hwnd:
            return None

        root_hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd
        if is_regular_hover_window(
            root_hwnd,
            exclude_own_window=exclude_own_window,
        ):
            return root_hwnd
    except Exception:
        pass
    return None


def get_active_root_window():
    """Return the current foreground window's top-level window."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        return win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd
    except Exception:
        return None


class CursorRaiseMonitor:
    """Detect a Ctrl press followed by cursor movement and raise one window."""

    def __init__(
        self,
        event_callback=None,
        poll_interval=DEFAULT_POLL_INTERVAL_SECONDS,
        movement_threshold=DEFAULT_MOVEMENT_THRESHOLD_PIXELS,
        key_state_provider=None,
        cursor_position_provider=None,
        target_provider=None,
        window_raiser=None,
        active_window_provider=None,
        suspension_provider=None,
        target_monitor_provider=None,
        activate_window=False,
        require_movement=True,
        exclude_own_window=True,
        suspension_check_interval=DEFAULT_SUSPENSION_CHECK_INTERVAL_SECONDS,
        clock=None,
    ):
        self.event_callback = event_callback
        self.poll_interval = max(0.01, float(poll_interval))
        self.movement_threshold = max(1, int(movement_threshold))
        self._key_state_provider = key_state_provider or _is_control_pressed
        self._cursor_position_provider = (
            cursor_position_provider or win32api.GetCursorPos
        )
        self.require_movement = bool(require_movement)
        self.exclude_own_window = bool(exclude_own_window)
        self._target_provider = target_provider or (
            lambda: get_window_under_cursor(self.exclude_own_window)
        )
        self.activate_window = bool(activate_window)
        self._window_raiser = window_raiser or (
            lambda hwnd: raise_regular_window(
                hwnd,
                False,
                self.activate_window,
            )
        )
        self._active_window_provider = (
            active_window_provider or get_active_root_window
        )
        self._suspension_provider = (
            suspension_provider
            or (
                lambda: get_suspending_windows(
                    self.exclude_own_window
                )
            )
        )
        self._target_monitor_provider = (
            target_monitor_provider or get_monitor_id_for_window
        )
        self._suspension_check_interval = max(
            0.05,
            float(suspension_check_interval),
        )
        self._clock = clock or time.monotonic
        self._stop_event = threading.Event()
        self._thread = None
        self._last_error = None
        self._suspended_monitors = {}
        self._session_blocked_monitors = set()
        self._session_raise_completed = False
        self._last_suspension_check = None
        self._reset_input_state()

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._last_error = None
        self._suspended_monitors = {}
        self._last_suspension_check = None
        self._reset_input_state()
        self._thread = threading.Thread(
            target=self._run,
            name="cursor-raise-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    def poll_once(self):
        ctrl_down = bool(self._key_state_provider())
        self._update_suspension(
            force=ctrl_down and not self._previous_ctrl_down
        )

        if not self._primed:
            self._primed = True
            self._previous_ctrl_down = ctrl_down
            return []

        if not ctrl_down:
            self._raise_on_ctrl_release()
            self._reset_session()
            self._previous_ctrl_down = False
            return []

        if not self._previous_ctrl_down:
            self._anchor = self._cursor_position_provider()
            self._armed = self._anchor is not None
            self._triggered = False
            self._session_blocked_monitors = set(self._suspended_monitors)
            if self.require_movement:
                self._previous_ctrl_down = ctrl_down
                return []

        if self._armed and not self._triggered:
            current = self._cursor_position_provider()
            if (
                not self.require_movement
                or self._moved_enough(self._anchor, current)
            ):
                return self._raise_current_target(ctrl_down)

        self._previous_ctrl_down = ctrl_down
        return []

    def _raise_current_target(self, ctrl_down):
        self._update_suspension(force=True)
        hwnd = self._target_provider()
        target_monitor_id = (
            self._target_monitor_provider(hwnd)
            if hwnd
            else ""
        )
        if (
            target_monitor_id
            and (
                target_monitor_id in self._suspended_monitors
                or target_monitor_id
                in self._session_blocked_monitors
            )
        ):
            self._session_blocked_monitors.add(target_monitor_id)
            self._previous_ctrl_down = ctrl_down
            return []
        active_hwnd = self._active_window_provider()
        if not hwnd or hwnd == active_hwnd:
            self._previous_ctrl_down = ctrl_down
            return []

        self._triggered = True
        window_title = get_window_title(hwnd)
        raised = False
        error_emitted = False
        started_at = time.monotonic()
        try:
            raised = bool(self._window_raiser(hwnd))
        except Exception as exc:
            duration_ms = (time.monotonic() - started_at) * 1000.0
            error_emitted = True
            self._emit(
                "error",
                "Ctrl 悬停提升失败: {0}".format(exc),
                hwnd,
                window_title,
                duration_ms,
            )

        duration_ms = (time.monotonic() - started_at) * 1000.0
        if raised:
            self._session_raise_completed = True
            self._emit(
                "raised",
                "已通过 Ctrl 悬停提升窗口",
                hwnd,
                window_title,
                duration_ms,
            )
            self._previous_ctrl_down = True
            return [hwnd]
        if not error_emitted:
            self._emit(
                "error",
                "Ctrl 悬停提升或激活失败，目标窗口可能以管理员权限运行",
                hwnd,
                window_title,
                duration_ms,
            )
        self._previous_ctrl_down = ctrl_down
        return []

    def _moved_enough(self, anchor, current):
        if anchor is None or current is None:
            return False
        delta_x = int(current[0]) - int(anchor[0])
        delta_y = int(current[1]) - int(anchor[1])
        threshold = self.movement_threshold
        return delta_x * delta_x + delta_y * delta_y >= threshold * threshold

    def _reset_input_state(self):
        self._primed = False
        self._previous_ctrl_down = False
        self._armed = False
        self._triggered = False
        self._anchor = None
        self._session_blocked_monitors = set()
        self._session_raise_completed = False

    def _reset_session(self):
        self._armed = False
        self._triggered = False
        self._anchor = None
        self._session_blocked_monitors = set()
        self._session_raise_completed = False

    def _raise_on_ctrl_release(self):
        if not self._session_raise_completed:
            return

        hwnd = self._active_window_provider()
        if not hwnd:
            return
        if self.exclude_own_window and is_current_process_window(hwnd):
            return

        started_at = time.monotonic()
        try:
            raised = bool(self._window_raiser(hwnd))
        except Exception as exc:
            duration_ms = (time.monotonic() - started_at) * 1000.0
            self._emit(
                "error",
                "松开 Ctrl 后提升当前激活窗口失败: {0}".format(exc),
                hwnd,
                get_window_title(hwnd),
                duration_ms,
            )
            return

        if not raised:
            duration_ms = (time.monotonic() - started_at) * 1000.0
            self._emit(
                "error",
                "松开 Ctrl 后提升当前激活窗口失败",
                hwnd,
                get_window_title(hwnd),
                duration_ms,
            )

    def _update_suspension(self, force=False):
        now = self._clock()
        if not force and (
            self._last_suspension_check is not None
            and now - self._last_suspension_check
            < self._suspension_check_interval
        ):
            return False

        self._last_suspension_check = now
        suspended_monitors = dict(self._suspension_provider() or {})
        if suspended_monitors == self._suspended_monitors:
            return False

        previous_ids = set(self._suspended_monitors)
        current_ids = set(suspended_monitors)
        self._suspended_monitors = suspended_monitors
        self._session_blocked_monitors.update(
            current_ids - previous_ids
        )

        for monitor_id in sorted(current_ids - previous_ids):
            self._emit(
                "paused",
                (
                    "检测到最大化、全屏或贴靠布局窗口，"
                    "已暂停显示器 {0} 上的 Ctrl 悬停提升"
                ).format(
                    format_monitor_name(monitor_id)
                ),
            )

        for monitor_id in sorted(previous_ids - current_ids):
            self._emit(
                "resumed",
                (
                    "显示器 {0} 已无最大化、全屏或贴靠布局窗口，"
                    "恢复 Ctrl 悬停提升"
                ).format(
                    format_monitor_name(monitor_id)
                ),
            )

    def _run(self):
        dpi_configured = enable_thread_dpi_awareness()
        self._emit(
            "started",
            "Ctrl 悬停提升已启动"
            if dpi_configured
            else "Ctrl 悬停提升已启动（DPI 感知不可用）",
        )

        while not self._stop_event.is_set():
            try:
                self.poll_once()
                self._last_error = None
            except Exception as exc:
                error = str(exc)
                if error != self._last_error:
                    self._last_error = error
                    self._emit("error", "Ctrl 悬停监听失败: {0}".format(error))
            self._stop_event.wait(self.poll_interval)

        self._emit("stopped", "Ctrl 悬停提升已停止")

    def _emit(
        self,
        kind,
        message,
        hwnd=0,
        window_title="",
        duration_ms=None,
    ):
        if self.event_callback is None:
            return
        try:
            self.event_callback(
                MonitorEvent(
                    kind,
                    message,
                    hwnd,
                    window_title,
                    duration_ms=duration_ms,
                )
            )
        except Exception:
            pass


def _is_control_pressed():
    return bool(win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000)
