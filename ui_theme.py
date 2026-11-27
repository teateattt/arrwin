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


def apply_native_window_style(root):
    """Apply title bar color and immersive dark mode through DWM."""
    if sys.platform != "win32" or pywinstyles is None:
        return False

    try:
        root.update_idletasks()
        version = sys.getwindowsversion()
        mode = getattr(root, "_arrwin_theme_mode", "light")
        palette = get_theme_palette(root)

        if version.build >= 22000:
            pywinstyles.apply_style(
                root,
                "dark" if mode == "dark" else "light",
            )
            pywinstyles.change_header_color(
                root,
                palette["titlebar"],
            )
            pywinstyles.change_title_color(
                root,
                palette["titlebar_text"],
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
