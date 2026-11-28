"""Sun Valley theme integration and native Windows chrome helpers."""

import ctypes
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

try:
    import darkdetect
except ImportError:
    darkdetect = None

try:
    import pywinstyles
except ImportError:
    pywinstyles = None

try:
    import sv_ttk
except ImportError:
    sv_ttk = None


FONT_FAMILY = "Microsoft YaHei UI"
THEME_PREFERENCES = ("system", "light", "dark")
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_COLOR_NONE = 0xFFFFFFFE
EXPERIMENTAL_CLIENT_MICA_ALPHA = 0.97

PALETTES = {
    "light": {
        "window_bg": "#fafafa",
        "surface": "#ffffff",
        "surface_alt": "#f3f3f3",
        "text": "#1a1a1a",
        "muted": "#616161",
        "accent": "#005fb8",
        "accent_text": "#ffffff",
        "border": "#d9d9d9",
        "titlebar": "#fafafa",
        "titlebar_text": "#1a1a1a",
    },
    "dark": {
        "window_bg": "#1c1c1c",
        "surface": "#202020",
        "surface_alt": "#272727",
        "text": "#fafafa",
        "muted": "#b3b3b3",
        "accent": "#60cdff",
        "accent_text": "#000000",
        "border": "#3a3a3a",
        "titlebar": "#1c1c1c",
        "titlebar_text": "#ffffff",
    },
}


def normalize_theme_preference(preference):
    """Return a supported theme preference, defaulting to system mode."""
    normalized = str(preference or "system").strip().lower()
    if normalized not in THEME_PREFERENCES:
        return "system"
    return normalized


def detect_system_theme():
    """Detect the Windows app theme, falling back to light mode."""
    if darkdetect is not None:
        detected = darkdetect.theme()
        if detected:
            detected = str(detected).lower()
            if detected in ("light", "dark"):
                return detected

    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion"
                r"\Themes\Personalize",
            )
            try:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            finally:
                winreg.CloseKey(key)
            return "light" if value else "dark"
        except (ImportError, OSError):
            pass
    return "light"


def resolve_theme_mode(preference="system", system_theme=None):
    """Resolve a user preference to the concrete light or dark theme."""
    normalized = normalize_theme_preference(preference)
    if normalized != "system":
        return normalized

    detected = system_theme or detect_system_theme()
    detected = str(detected).lower()
    return detected if detected in ("light", "dark") else "light"


def get_theme_palette(root):
    """Return the active semantic palette for a configured root window."""
    mode = getattr(root, "_arrwin_theme_mode", "light")
    return PALETTES.get(mode, PALETTES["light"])


def get_switch_checkbutton_style(root):
    """Use the themed switch when available and a regular checkbox otherwise."""
    style = ttk.Style(root)
    try:
        layout = style.layout("Switch.TCheckbutton")
    except tk.TclError:
        layout = None
    return (
        "Strong.Switch.TCheckbutton"
        if layout
        else "Strong.TCheckbutton"
    )


def hex_to_rgb(color):
    """Convert a six-digit hex color to an RGB tuple."""
    value = str(color).strip().lstrip("#")
    if len(value) != 6:
        raise ValueError("expected a six-digit hex color")
    return tuple(
        int(value[index:index + 2], 16)
        for index in range(0, 6, 2)
    )


def apply_app_theme(root, preference="system"):
    """Apply Sun Valley and the application's semantic ttk styles."""
    mode = resolve_theme_mode(preference)
    palette = PALETTES[mode]

    if sv_ttk is not None:
        sv_ttk.set_theme(mode, root=root)
    else:
        ttk.Style(root).theme_use("clam")

    style = ttk.Style(root)
    style.configure(
        ".",
        font=(FONT_FAMILY, 10, "bold"),
    )
    style.configure(
        "Accent.TLabel",
        foreground=palette["accent"],
    )
    style.configure(
        "Muted.TLabel",
        foreground=palette["muted"],
    )
    style.configure(
        "Status.TLabel",
        background=palette["window_bg"],
        foreground=palette["text"],
        font=(FONT_FAMILY, 10, "bold"),
        padding=(8, 6),
    )
    style.configure(
        "List.TFrame",
        background=palette["surface_alt"],
    )
    style.configure(
        "WindowRow.TFrame",
        background=palette["surface"],
    )
    style.configure(
        "TLabelframe.Label",
        foreground=palette["text"],
        font=(FONT_FAMILY, 10, "bold"),
    )
    style.configure(
        "Strong.TButton",
        font=(FONT_FAMILY, 10, "bold"),
        foreground=palette["text"],
        padding=(10, 5),
    )
    style.map(
        "Strong.TButton",
        foreground=[
            ("disabled", palette["muted"]),
            ("pressed", palette["text"]),
            ("active", palette["text"]),
        ],
    )
    style.configure(
        "Accent.TButton",
        font=(FONT_FAMILY, 10, "bold"),
        foreground=palette["accent_text"],
        padding=(10, 5),
    )
    style.map(
        "Accent.TButton",
        foreground=[
            ("disabled", palette["muted"]),
            ("pressed", palette["accent_text"]),
            ("active", palette["accent_text"]),
        ],
    )
    style.configure(
        "Strong.TLabel",
        font=(FONT_FAMILY, 10, "bold"),
        foreground=palette["text"],
    )
    style.configure(
        "Strong.TCheckbutton",
        font=(FONT_FAMILY, 10, "bold"),
        foreground=palette["text"],
        padding=(0, 2),
    )
    style.map(
        "Strong.TCheckbutton",
        foreground=[
            ("disabled", palette["muted"]),
            ("pressed", palette["text"]),
            ("active", palette["text"]),
            ("!disabled", palette["text"]),
        ],
    )
    style.configure(
        "Strong.Switch.TCheckbutton",
        font=(FONT_FAMILY, 10, "bold"),
        foreground=palette["text"],
        padding=(0, 2),
    )
    style.map(
        "Strong.Switch.TCheckbutton",
        foreground=[
            ("disabled", palette["muted"]),
            ("pressed", palette["text"]),
            ("active", palette["text"]),
            ("!disabled", palette["text"]),
        ],
    )
    style.configure(
        "TCombobox",
        font=(FONT_FAMILY, 10, "bold"),
    )
    style.configure(
        "Strong.TCombobox",
        font=(FONT_FAMILY, 10, "bold"),
        foreground=palette["text"],
        fieldbackground=palette["surface"],
        padding=(8, 4),
    )
    style.map(
        "Strong.TCombobox",
        foreground=[
            ("disabled", palette["muted"]),
            ("readonly", palette["text"]),
            ("focus", palette["text"]),
            ("active", palette["text"]),
        ],
        fieldbackground=[
            ("disabled", palette["surface_alt"]),
            ("readonly", palette["surface"]),
            ("focus", palette["surface"]),
            ("active", palette["surface"]),
        ],
    )
    root.option_add(
        "*TCombobox*Listbox.font",
        (FONT_FAMILY, 10, "bold"),
    )
    root.option_add(
        "*TCombobox*Listbox.background",
        palette["surface"],
    )
    root.option_add(
        "*TCombobox*Listbox.foreground",
        palette["text"],
    )
    root.option_add(
        "*TCombobox*Listbox.selectBackground",
        palette["accent"],
    )
    root.option_add(
        "*TCombobox*Listbox.selectForeground",
        palette["accent_text"],
    )

    if sv_ttk is None:
        style.configure(
            "Accent.TButton",
            background=palette["accent"],
        )

    root.configure(bg=palette["window_bg"])
    root._arrwin_theme_mode = mode
    root._arrwin_theme_palette = palette
    return mode


def refresh_combobox_popdown_style(root, combobox):
    """Update an existing Combobox popdown Listbox after a theme change."""
    palette = get_theme_palette(root)
    try:
        popdown = str(
            root.tk.call(
                "ttk::combobox::PopdownWindow",
                str(combobox),
            )
        )
        listbox = popdown + ".f.l"
        root.tk.call(
            listbox,
            "configure",
            "-font",
            "{{{0}}} 10 bold".format(FONT_FAMILY),
            "-background",
            palette["surface"],
            "-foreground",
            palette["text"],
            "-selectbackground",
            palette["accent"],
            "-selectforeground",
            palette["accent_text"],
        )
    except tk.TclError:
        pass


class SystemThemeFollower:
    """Poll Windows app theme while the main window is visible."""

    def __init__(
        self,
        root,
        preference="system",
        on_change=None,
        interval_ms=1500,
        initial_mode=None,
        theme_reader=None,
    ):
        self.root = root
        self.preference = normalize_theme_preference(preference)
        self.on_change = on_change
        self.interval_ms = interval_ms
        self._theme_reader = (
            theme_reader
            or (lambda: resolve_theme_mode(self.preference))
        )
        self._last_mode = initial_mode or self._theme_reader()
        self._after_id = None
        self._active = False

    @property
    def active(self):
        return self._active

    def start(self):
        if self._active:
            return
        self._active = True
        self._schedule()

    def suspend(self):
        self._active = False
        self._cancel_scheduled()

    def stop(self):
        self.suspend()

    def resume(self, refresh_now=True):
        if self._active:
            return
        if refresh_now:
            self.sync_now()
        self._active = True
        self._schedule()

    def sync_now(self):
        mode = self._theme_reader()
        if mode == self._last_mode:
            return mode

        self._last_mode = mode
        if self.on_change is not None:
            try:
                self.on_change(mode)
            except Exception as exc:
                print("切换系统主题失败: {0}".format(exc))
        return mode

    def _schedule(self):
        if not self._active or self._after_id is not None:
            return
        try:
            self._after_id = self.root.after(
                self.interval_ms,
                self._poll,
            )
        except tk.TclError:
            self._active = False

    def _cancel_scheduled(self):
        if self._after_id is None:
            return
        try:
            self.root.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self._after_id = None

    def _poll(self):
        self._after_id = None
        if not self._active:
            return
        self.sync_now()
        self._schedule()


def enable_dpi_awareness():
    """Enable Windows system DPI awareness before creating Tk."""
    if sys.platform != "win32":
        return False

    try:
        result = ctypes.windll.shcore.SetProcessDpiAwareness(1)
        if result == 0:
            return True
    except (AttributeError, OSError):
        pass

    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False


def configure_tk_scaling(root):
    """Align Tk scaling and core fonts with the active system DPI."""
    try:
        pixels_per_inch = float(round(root.winfo_fpixels("1i")))
        if pixels_per_inch > 0:
            root.tk.call("tk", "scaling", pixels_per_inch / 72.0)
    except (tk.TclError, ValueError):
        pass

    for font_name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkTooltipFont",
    ):
        try:
            tkfont.Font(
                root=root,
                name=font_name,
                exists=True,
            ).configure(
                family=FONT_FAMILY,
                size=10,
                weight="bold",
            )
        except tk.TclError:
            pass


def get_tk_window_handle(root):
    """Return the top-level Win32 handle for a Tk root window."""
    root.update_idletasks()
    client_handle = int(root.winfo_id())
    parent_handle = int(
        ctypes.windll.user32.GetParent(client_handle)
    )
    return parent_handle or client_handle


def set_dwm_attribute(hwnd, attribute, value):
    """Set an integer DWM window attribute and return success."""
    data = ctypes.c_uint(value & 0xFFFFFFFF)
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd,
        attribute,
        ctypes.byref(data),
        ctypes.sizeof(data),
    )
    return result == 0


def apply_native_window_style(root):
    """Apply Mica frame styling and native dark mode through DWM."""
    if sys.platform != "win32" or pywinstyles is None:
        return False

    try:
        version = sys.getwindowsversion()
        mode = getattr(root, "_arrwin_theme_mode", "light")
        palette = get_theme_palette(root)
        hwnd = get_tk_window_handle(root)
        client_mica_enabled = bool(
            getattr(root, "_arrwin_client_mica_enabled", False)
        )

        if version.build >= 22000:
            mica_applied = False
            if version.build >= 22621:
                # DWMSBT_MAINWINDOW (Mica).
                mica_applied = set_dwm_attribute(hwnd, 38, 2)
            if not mica_applied:
                pywinstyles.apply_style(root, "mica")

            # Keep the client area opaque while the frame uses Mica.
            set_dwm_attribute(
                hwnd,
                20,
                1 if mode == "dark" else 0,
            )
            # DWMWCP_ROUND.
            set_dwm_attribute(hwnd, 33, 2)
            # Let the system backdrop show through active title bars/borders.
            set_dwm_attribute(
                hwnd,
                DWMWA_CAPTION_COLOR,
                DWMWA_COLOR_NONE,
            )
            set_dwm_attribute(
                hwnd,
                DWMWA_BORDER_COLOR,
                DWMWA_COLOR_NONE,
            )
            pywinstyles.change_title_color(
                root,
                palette["titlebar_text"],
            )
            root.wm_attributes(
                "-alpha",
                (
                    EXPERIMENTAL_CLIENT_MICA_ALPHA
                    if client_mica_enabled
                    else 1.0
                ),
            )
        else:
            pywinstyles.apply_style(
                root,
                "dark" if mode == "dark" else "normal",
            )
            root.wm_attributes("-alpha", 0.99)
            root.wm_attributes("-alpha", 1.0)
        return True
    except Exception as exc:
        print("应用 Windows 标题栏主题失败: {0}".format(exc))
        return False
