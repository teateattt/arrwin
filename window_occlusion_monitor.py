"""Background monitor that raises ordinary windows after full occlusion."""

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import win32api
import win32con
import win32gui
import win32process


DEFAULT_SCAN_INTERVAL_SECONDS = 1.0
DEFAULT_COVERAGE_HOLD_SECONDS = 1.0
DEFAULT_POST_RAISE_COOLDOWN_SECONDS = 2.0
FULLSCREEN_RECT_TOLERANCE = 2
NON_APPLICATION_WINDOW_CLASSES = frozenset(
    {
        "#32769",
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
    }
)

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
LWA_ALPHA = 0x00000002
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

_user32.GetLayeredWindowAttributes.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.POINTER(wintypes.DWORD),
]
_user32.GetLayeredWindowAttributes.restype = wintypes.BOOL
_user32.GetDpiForWindow.argtypes = [wintypes.HWND]
_user32.GetDpiForWindow.restype = wintypes.UINT
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.AttachThreadInput.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.BOOL,
]
_user32.AttachThreadInput.restype = wintypes.BOOL
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.IsZoomed.restype = wintypes.BOOL
_user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
_user32.GetSystemMetricsForDpi.restype = ctypes.c_int
_dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


class _NativeRect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    @property
    def is_valid(self):
        return self.width > 0 and self.height > 0


@dataclass(frozen=True)
class MonitorWindow:
    hwnd: int
    title: str
    rect: WindowRect
    eligible: bool
    topmost: bool
    process_id: int = 0
    root_owner: int = 0
    maximized: bool = False
    fullscreen: bool = False
    monitor_id: str = ""


@dataclass(frozen=True)
class MonitorEvent:
    kind: str
    message: str
    hwnd: int = 0
    window_title: str = ""
    occurred_at: float = field(default_factory=time.time)
    duration_ms: Optional[float] = None


def format_monitor_event(event):
    """Format a monitor event for the status bar."""
    time_text = datetime.fromtimestamp(event.occurred_at).strftime(
        "%H:%M:%S.%f"
    )[:-3]
    parts = ["处理时间 {0}".format(time_text), event.message]
    if event.window_title:
        parts.append("窗口: {0}".format(event.window_title))
    if event.duration_ms is not None:
        parts.append("耗时: {0:.1f} ms".format(event.duration_ms))
    return " | ".join(parts)


def get_window_title(hwnd):
    """Return a window title without failing when the window has closed."""
    try:
        return win32gui.GetWindowText(hwnd).strip()
    except Exception:
        return ""


def is_current_process_window(hwnd, current_pid=None):
    """Return whether hwnd belongs to the current process."""
    try:
        _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
        expected_pid = (
            win32api.GetCurrentProcessId()
            if current_pid is None
            else current_pid
        )
        return window_pid == expected_pid
    except Exception:
        return False


@dataclass
class _CoverageState:
    covered_since: Optional[float] = None
    cooldown_until: float = 0.0


def is_fully_covered(target, blockers):
    """Return whether the union of blocker rectangles covers the target."""
    if not target.is_valid:
        return False

    clipped = []
    x_edges = {target.left, target.right}

    for blocker in blockers:
        left = max(target.left, blocker.left)
        top = max(target.top, blocker.top)
        right = min(target.right, blocker.right)
        bottom = min(target.bottom, blocker.bottom)

        if left < right and top < bottom:
            clipped_rect = WindowRect(left, top, right, bottom)
            clipped.append(clipped_rect)
            x_edges.add(left)
            x_edges.add(right)

    if not clipped:
        return False

    sorted_x = sorted(x_edges)
    for index in range(len(sorted_x) - 1):
        left = sorted_x[index]
        right = sorted_x[index + 1]
        if left >= right:
            continue

        y_ranges = [
            (rect.top, rect.bottom)
            for rect in clipped
            if rect.left <= left and rect.right >= right
        ]

        if not y_ranges:
            return False

        y_ranges.sort()
        covered_to = target.top
        for top, bottom in y_ranges:
            if bottom <= covered_to:
                continue
            if top > covered_to:
                break
            covered_to = bottom
            if covered_to >= target.bottom:
                break

        if covered_to < target.bottom:
            return False

    return True


def rectangles_almost_equal(left, right, tolerance=1):
    return (
        abs(left.left - right.left) <= tolerance
        and abs(left.top - right.top) <= tolerance
        and abs(left.right - right.right) <= tolerance
        and abs(left.bottom - right.bottom) <= tolerance
    )


def rectangles_intersect(left, right):
    return (
        left.left < right.right
        and left.right > right.left
        and left.top < right.bottom
        and left.bottom > right.top
    )


def covers_monitor(rect, monitor_rect, tolerance=FULLSCREEN_RECT_TOLERANCE):
    """Return whether a window's visual bounds fill its entire monitor."""
    return (
        rect.left <= monitor_rect.left + tolerance
        and rect.top <= monitor_rect.top + tolerance
        and rect.right >= monitor_rect.right - tolerance
        and rect.bottom >= monitor_rect.bottom - tolerance
    )


def format_monitor_name(monitor_id):
    """Return a compact display name for a monitor identifier."""
    if not monitor_id:
        return "未知显示器"
    return monitor_id.replace("\\\\.\\", "")


def find_suspending_windows(windows, foreground_hwnd=None):
    """Return one suspending app window for each affected monitor."""
    suspending = {}
    for window in windows:
        if not window.eligible or not window.monitor_id:
            continue
        foreground_fullscreen = (
            window.fullscreen
            and foreground_hwnd is not None
            and window.hwnd == foreground_hwnd
        )
        if not (window.maximized or foreground_fullscreen):
            continue
        suspending.setdefault(window.monitor_id, window)
    return suspending


def enable_thread_dpi_awareness():
    try:
        context = ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        previous = _user32.SetThreadDpiAwarenessContext(context)
        if isinstance(previous, int):
            return previous != 0
        return previous is not None and getattr(previous, "value", 0) != 0
    except (AttributeError, OSError, ValueError):
        return False


def _is_cloaked(hwnd):
    cloaked = wintypes.DWORD(0)
    result = _dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_CLOAKED,
        ctypes.byref(cloaked),
        ctypes.sizeof(cloaked),
    )
    return result == 0 and cloaked.value != 0


def _is_fully_transparent(hwnd):
    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if not ex_style & win32con.WS_EX_LAYERED:
        return False

    color_key = wintypes.DWORD(0)
    alpha = ctypes.c_ubyte(255)
    flags = wintypes.DWORD(0)
    success = _user32.GetLayeredWindowAttributes(
        hwnd,
        ctypes.byref(color_key),
        ctypes.byref(alpha),
        ctypes.byref(flags),
    )
    return bool(success) and bool(flags.value & LWA_ALPHA) and alpha.value == 0


def _get_visual_rect(hwnd):
    native_rect = _NativeRect()
    result = _dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(native_rect),
        ctypes.sizeof(native_rect),
    )
    if result == 0:
        rect = WindowRect(
            native_rect.left,
            native_rect.top,
            native_rect.right,
            native_rect.bottom,
        )
        if rect.is_valid:
            return rect

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return WindowRect(left, top, right, bottom)


def _get_monitor_details(hwnd):
    monitor = win32api.MonitorFromWindow(
        hwnd,
        win32con.MONITOR_DEFAULTTONEAREST,
    )
    info = win32api.GetMonitorInfo(monitor)
    left, top, right, bottom = info["Monitor"]
    monitor_id = info.get("Device") or "{0},{1}".format(left, top)
    return WindowRect(left, top, right, bottom), monitor_id


def get_monitor_id_for_window(hwnd):
    """Return the identifier of the monitor containing hwnd."""
    try:
        return _get_monitor_details(hwnd)[1]
    except Exception:
        return ""


def _get_foreground_root_window():
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        return win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd
    except Exception:
        return None


def is_eligible_monitor_window(ex_style, owner, window_class):
    """Return whether a window may be raised by the occlusion monitor."""
    topmost = bool(ex_style & win32con.WS_EX_TOPMOST)
    is_dialog = window_class == "#32770"
    return not (
        topmost
        or window_class in NON_APPLICATION_WINDOW_CLASSES
        or ex_style & win32con.WS_EX_TOOLWINDOW
        or ex_style & win32con.WS_EX_NOACTIVATE
        or (owner and not is_dialog)
    )


def same_window_group(target, blocker):
    """Return whether two windows belong to the same application group."""
    same_process = (
        target.process_id != 0
        and target.process_id == blocker.process_id
    )
    same_owner = (
        target.root_owner != 0
        and target.root_owner == blocker.root_owner
    )
    return same_process or same_owner


def _enumerate_monitor_windows(exclude_current_process=True):
    window_map = {}
    current_pid = win32api.GetCurrentProcessId()

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.IsIconic(hwnd):
                return True
            if _is_cloaked(hwnd) or _is_fully_transparent(hwnd):
                return True

            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if exclude_current_process and window_pid == current_pid:
                return True

            rect = _get_visual_rect(hwnd)
            if not rect.is_valid:
                return True

            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
            window_class = win32gui.GetClassName(hwnd)
            topmost = bool(ex_style & win32con.WS_EX_TOPMOST)
            root_owner = win32gui.GetAncestor(hwnd, win32con.GA_ROOTOWNER)
            if not root_owner:
                root_owner = hwnd
            maximized = bool(_user32.IsZoomed(hwnd))
            monitor_rect, monitor_id = _get_monitor_details(hwnd)
            fullscreen = bool(
                not maximized
                and covers_monitor(rect, monitor_rect)
            )
            eligible = is_eligible_monitor_window(
                ex_style,
                owner,
                window_class,
            )

            window_map[hwnd] = MonitorWindow(
                hwnd=hwnd,
                title=get_window_title(hwnd),
                rect=rect,
                eligible=eligible,
                topmost=topmost,
                process_id=window_pid,
                root_owner=root_owner,
                maximized=maximized,
                fullscreen=fullscreen,
                monitor_id=monitor_id,
            )
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)

    # Make the Z-order explicit instead of relying on EnumWindows ordering.
    ordered = []
    current = win32gui.GetTopWindow(None)
    while current:
        window = window_map.get(current)
        if window is not None:
            ordered.append(window)
        current = win32gui.GetWindow(current, win32con.GW_HWNDNEXT)

    if len(ordered) == len(window_map):
        return ordered
    return list(window_map.values())


def get_suspending_windows(exclude_current_process=True):
    """Return suspending app windows keyed by their monitor identifier."""
    return find_suspending_windows(
        _enumerate_monitor_windows(exclude_current_process),
        _get_foreground_root_window(),
    )


def _caption_height(hwnd):
    try:
        dpi = int(_user32.GetDpiForWindow(hwnd)) or 96
        caption = int(_user32.GetSystemMetricsForDpi(win32con.SM_CYCAPTION, dpi))
    except (AttributeError, OSError, ValueError):
        caption = int(win32api.GetSystemMetrics(win32con.SM_CYCAPTION))
    return max(32, caption + 8)


def _overlap_offset(hwnd, raw_rect):
    offset = _caption_height(hwnd)
    left, top, right, bottom = raw_rect

    try:
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        work_left, work_top, work_right, work_bottom = win32api.GetMonitorInfo(monitor)["Work"]
    except Exception:
        work_left, work_top, work_right, work_bottom = left, top, right, bottom

    if top + offset <= work_bottom - offset:
        return 0, offset
    if right + offset <= work_right:
        return offset, 0
    if left - offset >= work_left:
        return -offset, 0
    return 0, 0


def _has_regular_window_above(target_hwnd, target_rect):
    current = win32gui.GetTopWindow(None)
    while current:
        if current == target_hwnd:
            return False

        try:
            if win32gui.IsWindowVisible(current) and not win32gui.IsIconic(current):
                ex_style = win32gui.GetWindowLong(current, win32con.GWL_EXSTYLE)
                if (
                    not ex_style & win32con.WS_EX_TOPMOST
                    and not _is_cloaked(current)
                    and not _is_fully_transparent(current)
                ):
                    rect = _get_visual_rect(current)
                    if rect.is_valid and rectangles_intersect(target_rect, rect):
                        return True
        except Exception:
            pass

        current = win32gui.GetWindow(current, win32con.GW_HWNDNEXT)

    return True


def _activate_window(hwnd):
    """Bring hwnd to the foreground, using input attachment as a fallback."""
    _user32.BringWindowToTop(hwnd)
    _user32.SetForegroundWindow(hwnd)
    if _get_foreground_root_window() == hwnd:
        return True

    current_thread = win32api.GetCurrentThreadId()
    foreground_hwnd = win32gui.GetForegroundWindow()
    foreground_thread = 0
    if foreground_hwnd:
        foreground_thread, _ = win32process.GetWindowThreadProcessId(
            foreground_hwnd
        )
    target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
    attached_threads = []

    try:
        for thread_id in {foreground_thread, target_thread}:
            if (
                thread_id
                and thread_id != current_thread
                and _user32.AttachThreadInput(
                    current_thread,
                    thread_id,
                    True,
                )
            ):
                attached_threads.append(thread_id)

        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        return _get_foreground_root_window() == hwnd
    finally:
        for thread_id in attached_threads:
            _user32.AttachThreadInput(
                current_thread,
                thread_id,
                False,
            )


def raise_regular_window(hwnd, nudge_overlap=False, activate=False):
    """Raise a regular window, optionally activating it."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top

    flags = win32con.SWP_NOOWNERZORDER
    if not activate:
        flags |= win32con.SWP_NOACTIVATE
    if nudge_overlap and width > 0 and height > 0 and not _user32.IsZoomed(hwnd):
        offset_x, offset_y = _overlap_offset(
            hwnd,
            (left, top, right, bottom),
        )
        if offset_x or offset_y:
            left += offset_x
            top += offset_y
        else:
            flags |= win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
    else:
        flags |= win32con.SWP_NOMOVE | win32con.SWP_NOSIZE

    raised = bool(
        _user32.SetWindowPos(
            hwnd,
            win32con.HWND_TOP,
            left,
            top,
            width,
            height,
            flags,
        )
    )
    if not raised:
        return False

    target_rect = _get_visual_rect(hwnd)
    if not _has_regular_window_above(hwnd, target_rect):
        return _activate_window(hwnd) if activate else True

    # Some XAML-hosted windows report success for HWND_TOP without changing
    # their actual Z-order. A transient TopMost transition raises them
    # reliably; it is immediately removed to avoid persistent always-on-top.
    fixed_flags = (
        win32con.SWP_NOMOVE
        | win32con.SWP_NOSIZE
        | win32con.SWP_NOOWNERZORDER
    )
    if not activate:
        fixed_flags |= win32con.SWP_NOACTIVATE
    topmost_raised = bool(
        _user32.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            fixed_flags,
        )
    )
    if not topmost_raised:
        return False

    topmost_cleared = bool(
        _user32.SetWindowPos(
            hwnd,
            win32con.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            fixed_flags,
        )
    )
    if not topmost_cleared:
        _user32.SetWindowPos(
            hwnd,
            win32con.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            fixed_flags,
        )
        return False

    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if ex_style & win32con.WS_EX_TOPMOST:
        return False

    raised = not _has_regular_window_above(
        hwnd,
        _get_visual_rect(hwnd),
    )
    if not raised:
        return False
    return _activate_window(hwnd) if activate else True


def _raise_monitor_window(window, nudge_overlap):
    return raise_regular_window(window.hwnd, nudge_overlap)


class WindowOcclusionMonitor:
    def __init__(
        self,
        event_callback=None,
        scan_interval=DEFAULT_SCAN_INTERVAL_SECONDS,
        coverage_hold=DEFAULT_COVERAGE_HOLD_SECONDS,
        post_raise_cooldown=DEFAULT_POST_RAISE_COOLDOWN_SECONDS,
        nudge_overlap=False,
        exclude_own_window=True,
        window_provider=None,
        window_raiser=None,
        foreground_provider=None,
        clock=None,
    ):
        self.event_callback = event_callback
        self.scan_interval = max(0.1, float(scan_interval))
        self.coverage_hold = max(0.0, float(coverage_hold))
        self.post_raise_cooldown = max(0.0, float(post_raise_cooldown))
        self.nudge_overlap = bool(nudge_overlap)
        self.exclude_own_window = bool(exclude_own_window)
        self._window_provider = window_provider or (
            lambda: _enumerate_monitor_windows(self.exclude_own_window)
        )
        self._window_raiser = window_raiser or _raise_monitor_window
        self._foreground_provider = (
            foreground_provider or _get_foreground_root_window
        )
        self._clock = clock or time.monotonic
        self._states = {}
        self._auto_raised = set()
        self._stop_event = threading.Event()
        self._thread = None
        self._last_error = None
        self._suspended_monitor_ids = set()

    @property
    def auto_raise_suspended(self):
        return bool(self._suspended_monitor_ids)

    @property
    def suspended_monitor_ids(self):
        return frozenset(self._suspended_monitor_ids)

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._states.clear()
        self._auto_raised.clear()
        self._last_error = None
        self._suspended_monitor_ids = set()
        self._thread = threading.Thread(
            target=self._run,
            name="window-occlusion-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    def scan_once(self, now=None):
        now = self._clock() if now is None else now
        windows = self._window_provider()
        current_handles = {window.hwnd for window in windows}
        self._auto_raised.intersection_update(current_handles)
        self._states = {
            hwnd: state
            for hwnd, state in self._states.items()
            if hwnd in current_handles
        }

        suspending_windows = find_suspending_windows(
            windows,
            self._foreground_provider(),
        )
        self._update_suspension_gate(suspending_windows)

        selected = None
        selected_nudge = False

        for index, target in enumerate(windows):
            state = self._states.setdefault(target.hwnd, _CoverageState())
            if not target.eligible:
                state.covered_since = None
                continue
            if target.monitor_id in self._suspended_monitor_ids:
                state.covered_since = None
                continue

            # Owned popups and CEF helper windows must not complete external
            # coverage of another window in the same application group.
            blockers = [
                window
                for window in windows[:index]
                if not same_window_group(target, window)
            ]
            all_blockers = [window.rect for window in blockers]
            regular_blockers = [
                window for window in blockers if not window.topmost
            ]
            regular_rects = [window.rect for window in regular_blockers]

            if not is_fully_covered(target.rect, all_blockers):
                state.covered_since = None
                continue

            if not is_fully_covered(target.rect, regular_rects):
                state.covered_since = None
                continue

            # Avoid a ping-pong cascade between windows already brought forward
            # by this monitor.
            if any(window.hwnd in self._auto_raised for window in regular_blockers):
                state.covered_since = None
                continue

            if state.covered_since is None:
                state.covered_since = now

            if now - state.covered_since < self.coverage_hold:
                continue
            if now < state.cooldown_until:
                continue

            exact_overlap = any(
                rectangles_almost_equal(target.rect, blocker.rect)
                for blocker in regular_blockers
            )
            state.covered_since = None
            state.cooldown_until = now + self.post_raise_cooldown
            selected = target
            selected_nudge = self.nudge_overlap and exact_overlap
            break

        if selected is None:
            return []

        started_at = time.monotonic()
        try:
            raised = bool(self._window_raiser(selected, selected_nudge))
        except Exception as exc:
            duration_ms = (time.monotonic() - started_at) * 1000.0
            self._emit(
                "error",
                "提升窗口失败: {0}".format(exc),
                selected.hwnd,
                selected.title,
                duration_ms,
            )
            return []

        duration_ms = (time.monotonic() - started_at) * 1000.0
        if not raised:
            self._emit(
                "error",
                "提升窗口失败：未确认窗口层级变化",
                selected.hwnd,
                selected.title,
                duration_ms,
            )
            return []

        self._auto_raised.add(selected.hwnd)
        title = selected.title or "未命名窗口"
        self._emit(
            "raised",
            "自动提升被遮挡窗口",
            selected.hwnd,
            title,
            duration_ms,
        )
        return [selected.hwnd]

    def _update_suspension_gate(self, suspending_windows):
        current_ids = set(suspending_windows)
        if current_ids == self._suspended_monitor_ids:
            return

        previous_ids = self._suspended_monitor_ids
        self._suspended_monitor_ids = current_ids

        for monitor_id in sorted(current_ids - previous_ids):
            window = suspending_windows[monitor_id]
            self._emit(
                "paused",
                "检测到最大化或全屏窗口，已暂停显示器 {0} 上的自动提升".format(
                    format_monitor_name(monitor_id)
                ),
                window.hwnd,
                window.title,
            )

        for monitor_id in sorted(previous_ids - current_ids):
            self._emit(
                "resumed",
                "显示器 {0} 已无最大化或全屏窗口，恢复自动提升".format(
                    format_monitor_name(monitor_id)
                ),
            )

    def _run(self):
        dpi_configured = enable_thread_dpi_awareness()
        self._emit(
            "started",
            "监控已启动" if dpi_configured else "监控已启动（DPI 感知不可用）",
        )

        while not self._stop_event.is_set():
            try:
                self.scan_once()
                self._last_error = None
            except Exception as exc:
                error = str(exc)
                if error != self._last_error:
                    self._last_error = error
                    self._emit("error", "监控扫描失败: {0}".format(error))
            self._stop_event.wait(self.scan_interval)

        self._emit("stopped", "监控已停止")

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
