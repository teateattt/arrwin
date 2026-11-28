import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk, messagebox, scrolledtext
from PIL import Image, ImageTk
import win32gui
import win32process
import win32api
import win32con
import psutil
import io
import struct
import ctypes
import pythoncom
import queue
import sys
from window_occlusion_monitor import (
    WindowOcclusionMonitor,
    format_monitor_event,
    is_current_process_window,
)
from cursor_raise_monitor import CursorRaiseMonitor
from config_manager import ConfigManager
from startup_manager import (
    StartupManager,
    startup_requested,
)
from tray_manager import (
    APP_TITLE,
    SingleInstanceGuard,
    TrayController,
    resource_path,
    set_windows_app_id,
    show_single_instance_message,
)
from ui_theme import (
    FONT_FAMILY,
    SystemThemeFollower,
    apply_app_theme,
    apply_native_window_style,
    configure_tk_scaling,
    enable_dpi_awareness,
    get_theme_palette,
    get_switch_checkbutton_style,
    hex_to_rgb,
    normalize_theme_preference,
    refresh_combobox_popdown_style,
)
from ui_background import create_gradient_background
try:
    from pyvda import VirtualDesktop, get_virtual_desktops
    PYVDA_AVAILABLE = True
except ImportError:
    PYVDA_AVAILABLE = False
    print("pyvda 库不可用，将使用物理显示器")


STARTUP_INITIALIZATION_DELAY_MS = 20_000
WINDOW_TITLE = "Arrwin"
WINDOW_LIST_ROW_HEIGHT = 30
WINDOW_LIST_TOP_PADDING = 3


def is_monochrome_icon(image, minimum_pixels=8, max_channel_span=24):
    """Return whether opaque icon pixels form a low-saturation image."""
    opaque_pixels = [
        pixel[:3]
        for pixel in image.getdata()
        if pixel[3] >= 200
    ]
    if len(opaque_pixels) < minimum_pixels:
        return False
    average_span = sum(
        max(pixel) - min(pixel)
        for pixel in opaque_pixels
    ) / len(opaque_pixels)
    return average_span <= max_channel_span


class WindowArranger:
    def __init__(
        self,
        root,
        startup_mode=False,
        startup_manager=None,
        theme_mode="system",
    ):
        self.root = root
        self.window_list = []  # 存储 (hwnd, title, var) 的列表
        self.window_rows = {}
        self.window_list_icons = {}
        self._window_list_content_height = 1
        self.config_manager = ConfigManager()  # 初始化配置管理器
        self.startup_mode = startup_mode
        self.startup_manager = startup_manager or StartupManager()
        self.theme_preference = normalize_theme_preference(theme_mode)
        self.theme_mode = theme_mode
        self.theme_follower = None
        self._initial_width = None  # 记录初始宽度
        self._initial_height = None  # 记录初始高度
        self._is_resizing = False  # 是否正在调整大小
        self.desktop_var = tk.StringVar(value='全部桌面')  # 当前选择的桌面
        self.virtual_desktops = []  # 存储虚拟桌面 GUID 列表
        self.monitor_events = queue.Queue()
        self.tray_events = queue.Queue()
        self.occlusion_monitor = None
        self.cursor_raise_monitor = None
        self.tray_controller = None
        self._in_tray = False
        self._exiting = False
        self._tray_hint_shown = False
        self._last_window_state = 'normal'
        self._startup_initialization_id = None
        self._background_jobs = {}
        self._background_photos = {}
        self._background_items = {}
        self._background_keys = {}
        self._opacity_save_id = None

        # 绑定窗口大小改变事件
        self.root.bind('<Configure>', self.on_window_configure)
        # 绑定鼠标释放事件
        self.root.bind('<ButtonRelease-1>', self.on_mouse_release)
        self.root.bind('<Unmap>', self.on_window_unmap)

        self.setup_ui()
        self.theme_follower = SystemThemeFollower(
            self.root,
            preference=self.theme_preference,
            on_change=self.on_system_theme_changed,
            initial_mode=self.theme_mode,
        )
        self.root.after_idle(
            lambda: apply_native_window_style(self.root)
        )
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        tray_started = self.start_tray()
        self.root.after(250, self.poll_monitor_events)
        self.root.after(100, self.poll_tray_events)
        if self.startup_mode and tray_started:
            self._in_tray = True
            self.root.withdraw()
            self.status_var.set(
                '已从 Windows 启动，正在等待后台初始化...'
            )
            self._startup_initialization_id = self.root.after(
                STARTUP_INITIALIZATION_DELAY_MS,
                self.finish_startup_initialization,
            )
        else:
            self.refresh_monitor_list()
            self.refresh_window_list()
            if self.startup_mode:
                self.root.deiconify()
                self.status_var.set(
                    '系统托盘不可用，已显示主窗口'
                )
            self.root.after(0, self.start_saved_monitors)
            if self.theme_preference == 'system':
                self.theme_follower.start()

    def on_window_configure(self, event):
        """窗口配置改变事件（开始调整大小时）"""
        if event.widget != self.root or self._in_tray:
            return

        state = self.root.state()
        if state in ('normal', 'zoomed'):
            self._last_window_state = state

        current_width = self.root.winfo_width()
        current_height = self.root.winfo_height()
        if self._initial_width is None:
            self._initial_width = current_width
            self._initial_height = current_height
        if current_width != self._initial_width or current_height != self._initial_height:
            self._is_resizing = True

    def on_window_unmap(self, event):
        """Minimize to the notification area instead of the taskbar."""
        if event.widget != self.root or self._in_tray or self._exiting:
            return
        self.root.after_idle(self.hide_if_minimized)

    def hide_if_minimized(self):
        if self._in_tray or self._exiting:
            return
        try:
            if self.root.state() == 'iconic':
                self.hide_to_tray()
        except tk.TclError:
            pass

    def start_tray(self):
        tray = TrayController(
            resource_path('assets', 'arrwin-icon.png'),
            on_show=lambda: self.tray_events.put('show'),
            on_exit=lambda: self.tray_events.put('exit'),
            title=APP_TITLE,
        )
        if tray.start():
            self.tray_controller = tray
            return True
        return False

    def hide_to_tray(self):
        if self._exiting:
            return
        if self.tray_controller is None or not self.tray_controller.is_running:
            self.exit_app()
            return
        if self._in_tray:
            return

        if self.theme_follower is not None:
            self.theme_follower.suspend()

        try:
            state = self.root.state()
            if state in ('normal', 'zoomed'):
                self._last_window_state = state
            self._in_tray = True
            self.root.withdraw()
        except tk.TclError:
            self._in_tray = False
            return

        if not self._tray_hint_shown:
            self.tray_controller.notify(
                '程序仍在系统托盘中运行。双击托盘图标可恢复窗口，'
                '右键可选择退出。'
            )
            self._tray_hint_shown = True

    def show_from_tray(self):
        if self._exiting:
            return
        try:
            if (
                self.theme_follower is not None
                and self.theme_preference == 'system'
            ):
                self.theme_follower.sync_now()
            self._in_tray = False
            self.root.deiconify()
            if self._last_window_state == 'zoomed':
                self.root.state('zoomed')
            self.root.lift()
            self.root.focus_force()
            self.root.after_idle(
                lambda: apply_native_window_style(self.root)
            )
            if (
                self.theme_follower is not None
                and self.theme_preference == 'system'
            ):
                self.theme_follower.resume(refresh_now=False)
        except tk.TclError:
            pass

    def poll_tray_events(self):
        while True:
            try:
                event = self.tray_events.get_nowait()
            except queue.Empty:
                break

            if event == 'show':
                self.show_from_tray()
            elif event == 'exit':
                self.exit_app()
                return

        if not self._exiting:
            self.root.after(100, self.poll_tray_events)

    def on_mouse_release(self, event):
        """鼠标释放事件（调整大小结束后）"""
        if self._is_resizing:
            # 获取实际窗口尺寸
            actual_width = self.root.winfo_width()
            actual_height = self.root.winfo_height()
            print(f"鼠标释放，保存窗口尺寸: {actual_width}x{actual_height}")
            self.config_manager.set_window_size(actual_width, actual_height)
            self._initial_width = actual_width
            self._initial_height = actual_height
            self._is_resizing = False

    def on_system_theme_changed(self, mode):
        """Reapply the theme and theme-dependent controls at runtime."""
        if self._exiting or mode == self.theme_mode:
            return

        try:
            first_visible = self.window_list_canvas.yview()[0]
        except (AttributeError, tk.TclError):
            first_visible = None

        self.theme_mode = apply_app_theme(self.root, mode)
        palette = get_theme_palette(self.root)
        if hasattr(self, 'background_canvas'):
            self.background_canvas.configure(
                bg=palette['window_bg'],
            )
        if hasattr(self, 'window_list_canvas'):
            self.window_list_canvas.configure(
                bg=palette['surface_alt'],
            )
        refresh_combobox_popdown_style(
            self.root,
            self.desktop_combobox,
        )
        self.refresh_window_list()
        self.root.after_idle(self.layout_background_content)
        self.root.after_idle(
            lambda: self.schedule_background_refresh(
                self.background_canvas,
                'main',
            )
        )
        self.root.after_idle(
            lambda: self.schedule_background_refresh(
                self.window_list_canvas,
                'list',
            )
        )
        if first_visible is not None:
            self.root.after_idle(
                lambda: self.window_list_canvas.yview_moveto(
                    first_visible
                )
            )
        self.root.after_idle(
            lambda: apply_native_window_style(self.root)
        )

    def on_background_canvas_configure(self, event):
        if event.widget is not self.background_canvas:
            return
        self.layout_background_content()
        self.schedule_background_refresh(
            self.background_canvas,
            'main',
        )

    def layout_background_content(self):
        """Place content sections above the generated gradient canvas."""
        required = (
            'background_canvas',
            'settings_frame',
            'action_frame',
            'status_bar',
            'invert_selection_button',
            'deselect_all_button',
            'refresh_list_button',
            'window_list_canvas',
            'list_scrollbar',
        )
        if not all(hasattr(self, name) for name in required):
            return

        self.background_canvas.update_idletasks()
        width = self.background_canvas.winfo_width()
        height = self.background_canvas.winfo_height()
        if width < 200 or height < 300:
            return

        margin = 12
        gap = 10
        available_width = max(200, width - 2 * margin)
        settings_height = self.settings_frame.winfo_reqheight()
        status_height = self.status_bar.winfo_reqheight()
        action_height = self.action_frame.winfo_reqheight()
        controls_height = max(
            self.invert_selection_button.winfo_reqheight(),
            self.deselect_all_button.winfo_reqheight(),
            self.refresh_list_button.winfo_reqheight(),
        )
        scrollbar_width = self.list_scrollbar.winfo_reqwidth()

        controls_y = margin + settings_height + gap
        list_top = controls_y + controls_height + gap
        status_y = max(
            list_top + 140,
            height - margin - status_height,
        )
        action_y = max(
            list_top + 100,
            status_y - gap - action_height,
        )
        list_height = max(100, action_y - gap - list_top)

        self.settings_frame.place(
            x=margin,
            y=margin,
            width=available_width,
        )
        right_edge = margin + available_width
        refresh_width = self.refresh_list_button.winfo_reqwidth()
        deselect_width = self.deselect_all_button.winfo_reqwidth()
        refresh_x = right_edge - refresh_width
        deselect_x = refresh_x - gap - deselect_width
        invert_x = margin + int(available_width * 0.28)
        self.invert_selection_button.place(
            x=invert_x,
            y=controls_y,
        )
        self.deselect_all_button.place(
            x=deselect_x,
            y=controls_y,
        )
        self.refresh_list_button.place(
            x=refresh_x,
            y=controls_y,
        )
        list_width = max(
            120,
            available_width - scrollbar_width,
        )
        self.window_list_canvas.place(
            x=margin,
            y=list_top,
            width=list_width,
            height=list_height,
        )
        self.list_scrollbar.place(
            x=margin + list_width,
            y=list_top,
            height=list_height,
        )
        self.action_frame.place(
            relx=0.5,
            y=action_y,
            anchor='n',
        )
        self.status_bar.place(
            x=margin,
            y=status_y,
            width=available_width,
        )

    def schedule_background_refresh(self, canvas, key):
        """Debounce gradient regeneration for a canvas."""
        previous = self._background_jobs.pop(key, None)
        if previous is not None:
            try:
                self.root.after_cancel(previous)
            except tk.TclError:
                pass
        self._background_jobs[key] = self.root.after(
            120,
            lambda: self.render_background(canvas, key),
        )

    def render_background(self, canvas, key):
        """Generate and cache the current theme's gradient background."""
        self._background_jobs.pop(key, None)
        try:
            if not canvas.winfo_exists():
                return
            width = max(1, canvas.winfo_width())
            height = max(1, canvas.winfo_height())
            cache_key = (key, width, height, self.theme_mode)
            if (
                self._background_keys.get(key) == cache_key
                and key in self._background_photos
            ):
                self._place_background_item(canvas, key)
                return

            image = create_gradient_background(
                resource_path('assets', 'bg.png'),
                (width, height),
                self.theme_mode,
            )
            photo = ImageTk.PhotoImage(image, master=self.root)
            self._background_photos[key] = photo
            self._background_keys[key] = cache_key
            self._place_background_item(canvas, key)
        except Exception as exc:
            print("生成界面渐变背景失败: {0}".format(exc))

    def _place_background_item(self, canvas, key):
        photo = self._background_photos.get(key)
        if photo is None:
            return

        item_id = self._background_items.get(key)
        if item_id is None:
            item_id = canvas.create_image(
                0,
                0,
                anchor='nw',
                image=photo,
                tags=('gradient_background',),
            )
            self._background_items[key] = item_id
        else:
            canvas.itemconfigure(item_id, image=photo)

        if key == 'list':
            self.keep_list_background_visible()
        canvas.tag_lower(item_id)

    def keep_list_background_visible(self):
        """Keep the list gradient fixed to the visible canvas viewport."""
        item_id = self._background_items.get('list')
        if item_id is None:
            return
        try:
            self.window_list_canvas.coords(
                item_id,
                0,
                self.window_list_canvas.canvasy(0),
            )
            self.window_list_canvas.tag_lower(item_id)
        except tk.TclError:
            pass

    def on_list_canvas_scroll(self, *args):
        self.window_list_canvas.yview(*args)
        self.keep_list_background_visible()

    def on_list_canvas_configure(self, event):
        if event.widget is not self.window_list_canvas:
            return
        self.update_window_list_scrollregion()
        self.schedule_background_refresh(
            self.window_list_canvas,
            'list',
        )

    def update_window_list_scrollregion(self):
        canvas = self.window_list_canvas
        width = max(1, canvas.winfo_width())
        height = max(
            self._window_list_content_height,
            canvas.winfo_height(),
            1,
        )
        canvas.configure(scrollregion=(0, 0, width, height))
        self.keep_list_background_visible()

    def setup_ui(self):
        """初始化界面"""
        self.root._arrwin_client_mica_enabled = (
            self.config_manager.get_experimental_client_mica()
        )
        self.root._arrwin_client_mica_alpha = (
            self.config_manager.get_window_opacity() / 100.0
        )
        self.theme_mode = apply_app_theme(
            self.root,
            self.theme_preference,
        )
        palette = get_theme_palette(self.root)
        switch_style = get_switch_checkbutton_style(self.root)

        self.root.title(WINDOW_TITLE)
        try:
            self.root.iconbitmap(resource_path('assets', 'arrwin.ico'))
        except tk.TclError:
            pass

        # 从配置文件加载窗口尺寸
        width = max(self.config_manager.get_window_width(), 820)
        height = self.config_manager.get_window_height()
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(820, 600)
        self.root.resizable(True, True)

        # 背景画布承载渐变，内容区块置于其上层
        self.background_canvas = tk.Canvas(
            self.root,
            highlightthickness=0,
            borderwidth=0,
            bg=palette['window_bg'],
        )
        self.background_canvas.grid(
            row=0,
            column=0,
            sticky=(tk.W, tk.E, tk.N, tk.S),
        )
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.background_canvas.bind(
            '<Configure>',
            self.on_background_canvas_configure,
        )

        # 设置区域
        self.settings_frame = ttk.Frame(
            self.background_canvas,
            style='Panel.TFrame',
            padding="12",
        )
        self.settings_frame.columnconfigure(1, weight=1)
        settings_frame = self.settings_frame

        # 偏移量设置
        ttk.Label(
            settings_frame,
            text='窗口偏移量 (像素):',
            style='Strong.TLabel',
        ).grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        self.offset_var = tk.IntVar(
            value=self.config_manager.get_window_offset()
        )
        offset_spinbox = ttk.Spinbox(
            settings_frame,
            from_=10,
            to=200,
            textvariable=self.offset_var,
            width=10,
            increment=5
        )
        offset_spinbox.grid(row=0, column=1, sticky=tk.W, padx=(0, 20))
        offset_spinbox.configure(command=self.on_window_offset_changed)
        offset_spinbox.bind('<FocusOut>', self.on_window_offset_changed)
        offset_spinbox.bind('<Return>', self.on_window_offset_changed)

        # 操作说明
        info_label = ttk.Label(
            settings_frame,
            text='提示: 在接近左上角的位置，窗口阶梯排列，不改变窗口大小',
            font=(FONT_FAMILY, 10),
            style='Muted.TLabel',
        )
        info_label.grid(
            row=0,
            column=2,
            columnspan=2,
            sticky=tk.W,
        )

        # 桌面选择下拉框（新添加）
        ttk.Label(
            settings_frame,
            text='选择桌面:',
            style='Strong.TLabel',
        ).grid(row=1, column=0, sticky=tk.W, padx=(0, 10), pady=(10, 0))
        self.desktop_combobox = ttk.Combobox(
            settings_frame,
            textvariable=self.desktop_var,
            state='readonly',
            width=20,
            style='Strong.TCombobox',
            font=(FONT_FAMILY, 10, 'bold'),
        )
        self.desktop_combobox.grid(row=1, column=1, sticky=tk.W, padx=(0, 20), pady=(10, 0))
        # 绑定选择变化事件
        self.desktop_combobox.bind('<<ComboboxSelected>>', self.on_desktop_changed)
        ttk.Label(
            settings_frame,
            text='透明度:',
            style='Strong.TLabel',
        ).grid(
            row=1,
            column=2,
            sticky=tk.E,
            padx=(0, 8),
            pady=(10, 0),
        )
        self.opacity_var = tk.DoubleVar(
            value=self.config_manager.get_window_opacity()
        )
        opacity_controls = ttk.Frame(
            settings_frame,
            style='Panel.TFrame',
        )
        opacity_controls.grid(
            row=1,
            column=3,
            sticky=tk.W,
            pady=(10, 0),
        )
        self.opacity_scale = ttk.Scale(
            opacity_controls,
            from_=85,
            to=100,
            orient=tk.HORIZONTAL,
            length=110,
            variable=self.opacity_var,
            command=self.on_opacity_changed,
        )
        self.opacity_scale.pack(side=tk.LEFT)
        self.opacity_value_label = ttk.Label(
            opacity_controls,
            text='{0}%'.format(int(self.opacity_var.get())),
            width=5,
            anchor=tk.W,
            style='Strong.TLabel',
        )
        self.opacity_value_label.pack(side=tk.LEFT, padx=(6, 0))

        # 自动显示被完全遮挡的窗口
        ttk.Label(
            settings_frame,
            text='遮挡监控:',
            style='Strong.TLabel',
        ).grid(
            row=2,
            column=0,
            sticky=tk.W,
            padx=(0, 10),
            pady=(10, 0)
        )
        self.auto_raise_var = tk.BooleanVar(
            value=self.config_manager.get_auto_raise_enabled()
        )
        self.auto_raise_check = ttk.Checkbutton(
            settings_frame,
            text='自动显示被完全遮挡的窗口',
            variable=self.auto_raise_var,
            command=self.toggle_auto_monitor,
            style=switch_style,
        )
        self.auto_raise_check.grid(
            row=2,
            column=1,
            columnspan=2,
            sticky=tk.W,
            padx=(0, 20),
            pady=(10, 0)
        )

        self.overlap_nudge_var = tk.BooleanVar(
            value=self.config_manager.get_auto_raise_nudge_overlap()
        )
        self.overlap_nudge_check = ttk.Checkbutton(
            settings_frame,
            text='完全重叠时错开标题栏',
            variable=self.overlap_nudge_var,
            command=self.on_overlap_nudge_changed,
            style='Strong.TCheckbutton',
        )
        self.overlap_nudge_check.grid(
            row=3,
            column=2,
            sticky=tk.W,
            pady=(10, 0)
        )
        self.auto_raise_exclude_self_var = tk.BooleanVar(
            value=self.config_manager.get_auto_raise_exclude_self()
        )
        self.auto_raise_exclude_self_check = ttk.Checkbutton(
            settings_frame,
            text='排除本程序自身窗口',
            variable=self.auto_raise_exclude_self_var,
            command=self.on_auto_exclude_self_changed,
            style='Strong.TCheckbutton',
        )
        self.auto_raise_exclude_self_check.grid(
            row=3,
            column=1,
            sticky=tk.W,
            padx=(0, 20),
            pady=(10, 0)
        )
        self.update_auto_option_states()

        ttk.Label(
            settings_frame,
            text='Ctrl 悬停提升:',
            style='Strong.TLabel',
        ).grid(
            row=4,
            column=0,
            sticky=tk.W,
            padx=(0, 10),
            pady=(10, 0)
        )
        self.ctrl_move_raise_var = tk.BooleanVar(
            value=self.config_manager.get_ctrl_move_raise_enabled()
        )
        self.ctrl_move_raise_check = ttk.Checkbutton(
            settings_frame,
            text='启用 Ctrl 悬停提升',
            variable=self.ctrl_move_raise_var,
            command=self.toggle_ctrl_move_raise,
            style=switch_style,
        )
        self.ctrl_move_raise_check.grid(
            row=4,
            column=1,
            columnspan=2,
            sticky=tk.W,
            padx=(0, 20),
            pady=(10, 0)
        )

        self.ctrl_move_required_var = tk.BooleanVar(
            value=self.config_manager.get_ctrl_move_required()
        )
        self.ctrl_move_required_check = ttk.Checkbutton(
            settings_frame,
            text='必须移动鼠标后触发',
            variable=self.ctrl_move_required_var,
            command=self.on_ctrl_move_required_changed,
            style='Strong.TCheckbutton',
        )
        self.ctrl_move_required_check.grid(
            row=5,
            column=1,
            sticky=tk.W,
            padx=(0, 20),
            pady=(10, 0)
        )

        self.ctrl_move_raise_activate_var = tk.BooleanVar(
            value=self.config_manager.get_ctrl_move_raise_activate()
        )
        self.ctrl_move_raise_activate_check = ttk.Checkbutton(
            settings_frame,
            text='提升时同时激活目标窗口',
            variable=self.ctrl_move_raise_activate_var,
            command=self.on_ctrl_move_activate_changed,
            style='Strong.TCheckbutton',
        )
        self.ctrl_move_raise_activate_check.grid(
            row=5,
            column=2,
            sticky=tk.W,
            pady=(10, 0)
        )

        self.ctrl_move_raise_exclude_self_var = tk.BooleanVar(
            value=self.config_manager.get_ctrl_move_raise_exclude_self()
        )
        self.ctrl_move_raise_exclude_self_check = ttk.Checkbutton(
            settings_frame,
            text='排除本程序自身窗口',
            variable=self.ctrl_move_raise_exclude_self_var,
            command=self.on_ctrl_exclude_self_changed,
            style='Strong.TCheckbutton',
        )
        self.ctrl_move_raise_exclude_self_check.grid(
            row=6,
            column=1,
            columnspan=2,
            sticky=tk.W,
            padx=(0, 20),
            pady=(10, 0)
        )
        self.update_ctrl_option_states()

        ttk.Label(
            settings_frame,
            text='系统启动:',
            style='Strong.TLabel',
        ).grid(
            row=7,
            column=0,
            sticky=tk.W,
            padx=(0, 10),
            pady=(10, 0)
        )
        startup_enabled = False
        try:
            startup_enabled = self.startup_manager.is_enabled()
        except Exception as exc:
            print("读取自动启动设置失败: {0}".format(exc))
        self.startup_var = tk.BooleanVar(value=startup_enabled)
        self.startup_check = ttk.Checkbutton(
            settings_frame,
            text='登录 Windows 时自动启动',
            variable=self.startup_var,
            command=self.toggle_startup,
            style=switch_style,
        )
        self.startup_check.grid(
            row=7,
            column=1,
            columnspan=2,
            sticky=tk.W,
            padx=(0, 20),
            pady=(10, 0)
        )

        # 窗口列表操作按钮直接放在背景画布上
        self.invert_selection_button = ttk.Button(
            self.background_canvas,
            text='反选',
            command=self.invert_selection,
            width=10,
            style='Strong.TButton',
        )
        self.deselect_all_button = ttk.Button(
            self.background_canvas,
            text='全不选',
            command=self.deselect_all,
            width=10,
            style='Strong.TButton',
        )
        self.refresh_list_button = ttk.Button(
            self.background_canvas,
            text='刷新列表',
            command=self.refresh_window_list,
            width=10,
            style='Strong.TButton',
        )

        # 列表本身直接放在背景画布上
        self.window_list_canvas = tk.Canvas(
            self.background_canvas,
            highlightthickness=0,
            borderwidth=0,
            bg=palette['surface_alt'],
        )
        canvas = self.window_list_canvas
        self.list_scrollbar = ttk.Scrollbar(
            self.background_canvas,
            orient="vertical",
            command=self.on_list_canvas_scroll,
        )
        canvas.configure(yscrollcommand=self.list_scrollbar.set)
        canvas.bind(
            '<Configure>',
            self.on_list_canvas_configure,
            add='+',
        )

        # 鼠标滚轮支持
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            self.keep_list_background_visible()
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self.root.after_idle(
            lambda: self.schedule_background_refresh(
                self.window_list_canvas,
                'list',
            )
        )

        # 操作按钮区域
        self.action_frame = ttk.Frame(self.background_canvas)
        action_frame = self.action_frame

        # 开始排列按钮
        self.arrange_btn = ttk.Button(
            action_frame,
            text='开始排列',
            command=self.arrange_selected_windows,
            width=20,
            style='Accent.TButton',
        )
        self.arrange_btn.grid(row=0, column=0, padx=(0, 10))

        # 退出按钮
        ttk.Button(
            action_frame,
            text='退出',
            command=self.exit_app,
            width=10,
            style='Strong.TButton',
        ).grid(row=0, column=1)

        # 状态栏
        self.status_var = tk.StringVar(value='就绪')
        self.status_bar = ttk.Label(
            self.background_canvas,
            textvariable=self.status_var,
            style='Status.TLabel',
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=max(300, width - 40),
        )
        self.root.after_idle(self.layout_background_content)
        self.root.after_idle(
            lambda: self.schedule_background_refresh(
                self.background_canvas,
                'main',
            )
        )

    def start_saved_monitors(self):
        """Restore monitor state after the window has been created."""
        if self.auto_raise_var.get():
            self.start_auto_monitor()
        if self.ctrl_move_raise_var.get():
            self.start_ctrl_move_monitor()

    def finish_startup_initialization(self):
        """Load window data and monitors after the login startup delay."""
        self._startup_initialization_id = None
        if self._exiting:
            return
        self.status_var.set('后台初始化完成，正在恢复监控...')
        self.refresh_monitor_list()
        self.refresh_window_list()
        self.start_saved_monitors()

    def toggle_startup(self):
        enabled = self.startup_var.get()
        try:
            self.startup_manager.set_enabled(enabled)
        except Exception as exc:
            self.startup_var.set(not enabled)
            messagebox.showerror(
                '错误',
                '更新自动启动设置失败：\n{0}'.format(exc)
            )
            self.status_var.set('自动启动设置更新失败')
            return

        if enabled:
            self.status_var.set('已启用登录 Windows 时自动启动')
        else:
            self.status_var.set('已关闭登录 Windows 时自动启动')

    def on_window_offset_changed(self, event=None):
        del event
        try:
            offset = max(10, min(200, int(self.offset_var.get())))
        except (tk.TclError, TypeError, ValueError):
            offset = self.config_manager.get_window_offset()
        self.offset_var.set(offset)
        self.config_manager.set_window_offset(offset)

    def on_opacity_changed(self, value=None):
        del value
        try:
            opacity = max(85, min(100, int(round(self.opacity_var.get()))))
        except (tk.TclError, TypeError, ValueError):
            opacity = self.config_manager.get_window_opacity()

        self.opacity_var.set(opacity)
        self.opacity_value_label.configure(
            text='{0}%'.format(opacity),
        )
        self.root._arrwin_client_mica_alpha = opacity / 100.0
        if (
            self.root._arrwin_client_mica_enabled
            and sys.platform == 'win32'
            and sys.getwindowsversion().build >= 22000
        ):
            self.root.wm_attributes('-alpha', opacity / 100.0)

        if self._opacity_save_id is not None:
            try:
                self.root.after_cancel(self._opacity_save_id)
            except tk.TclError:
                pass
        self._opacity_save_id = self.root.after(
            250,
            self.save_opacity_setting,
        )

    def save_opacity_setting(self):
        self._opacity_save_id = None
        self.config_manager.set_window_opacity(
            int(self.opacity_var.get())
        )

    def update_auto_option_states(self):
        state = tk.NORMAL if self.auto_raise_var.get() else tk.DISABLED
        self.overlap_nudge_check.configure(state=state)
        self.auto_raise_exclude_self_check.configure(state=state)

    def update_ctrl_option_states(self):
        state = (
            tk.NORMAL
            if self.ctrl_move_raise_var.get()
            else tk.DISABLED
        )
        self.ctrl_move_required_check.configure(state=state)
        self.ctrl_move_raise_activate_check.configure(state=state)
        self.ctrl_move_raise_exclude_self_check.configure(state=state)

    def toggle_auto_monitor(self):
        enabled = self.auto_raise_var.get()
        self.config_manager.set_auto_raise_enabled(enabled)
        self.update_auto_option_states()
        if enabled:
            self.start_auto_monitor()
        else:
            self.stop_auto_monitor()
            self.status_var.set('遮挡监控已暂停')

    def on_overlap_nudge_changed(self):
        enabled = self.overlap_nudge_var.get()
        self.config_manager.set_auto_raise_nudge_overlap(enabled)
        if self.occlusion_monitor is not None:
            self.occlusion_monitor.nudge_overlap = enabled

    def on_auto_exclude_self_changed(self):
        enabled = self.auto_raise_exclude_self_var.get()
        self.config_manager.set_auto_raise_exclude_self(enabled)
        if self.occlusion_monitor is not None:
            self.occlusion_monitor.exclude_own_window = enabled

    def toggle_ctrl_move_raise(self):
        enabled = self.ctrl_move_raise_var.get()
        self.config_manager.set_ctrl_move_raise_enabled(enabled)
        self.update_ctrl_option_states()
        if enabled:
            self.start_ctrl_move_monitor()
        else:
            self.stop_ctrl_move_monitor()
            self.status_var.set('Ctrl 悬停提升已暂停')

    def on_ctrl_move_required_changed(self):
        required = self.ctrl_move_required_var.get()
        self.config_manager.set_ctrl_move_required(required)
        if self.cursor_raise_monitor is not None:
            self.cursor_raise_monitor.require_movement = required

    def on_ctrl_move_activate_changed(self):
        enabled = self.ctrl_move_raise_activate_var.get()
        self.config_manager.set_ctrl_move_raise_activate(enabled)
        if self.cursor_raise_monitor is not None:
            self.cursor_raise_monitor.activate_window = enabled

    def on_ctrl_exclude_self_changed(self):
        enabled = self.ctrl_move_raise_exclude_self_var.get()
        self.config_manager.set_ctrl_move_raise_exclude_self(enabled)
        if self.cursor_raise_monitor is not None:
            self.cursor_raise_monitor.exclude_own_window = enabled

    def start_ctrl_move_monitor(self):
        if (
            self.cursor_raise_monitor is not None
            and self.cursor_raise_monitor.is_running
        ):
            return

        try:
            self.cursor_raise_monitor = CursorRaiseMonitor(
                event_callback=self.monitor_events.put,
                activate_window=self.ctrl_move_raise_activate_var.get(),
                require_movement=self.ctrl_move_required_var.get(),
                exclude_own_window=self.ctrl_move_raise_exclude_self_var.get(),
            )
            self.cursor_raise_monitor.start()
            self.status_var.set('Ctrl 悬停提升启动中...')
        except Exception as exc:
            self.cursor_raise_monitor = None
            self.ctrl_move_raise_var.set(False)
            self.config_manager.set_ctrl_move_raise_enabled(False)
            self.update_ctrl_option_states()
            messagebox.showerror(
                '错误',
                '启动 Ctrl 悬停提升失败：\n{0}'.format(exc)
            )
            self.status_var.set('Ctrl 悬停提升启动失败')

    def stop_ctrl_move_monitor(self):
        if self.cursor_raise_monitor is None:
            return
        try:
            self.cursor_raise_monitor.stop()
        finally:
            self.cursor_raise_monitor = None

    def start_auto_monitor(self):
        if self.occlusion_monitor is not None and self.occlusion_monitor.is_running:
            return

        try:
            self.occlusion_monitor = WindowOcclusionMonitor(
                event_callback=self.monitor_events.put,
                nudge_overlap=self.overlap_nudge_var.get(),
                exclude_own_window=self.auto_raise_exclude_self_var.get(),
            )
            self.occlusion_monitor.start()
            self.status_var.set('遮挡监控启动中...')
        except Exception as exc:
            self.occlusion_monitor = None
            self.auto_raise_var.set(False)
            self.config_manager.set_auto_raise_enabled(False)
            self.update_auto_option_states()
            messagebox.showerror('错误', f'启动遮挡监控失败：\n{exc}')
            self.status_var.set('遮挡监控启动失败')

    def stop_auto_monitor(self):
        if self.occlusion_monitor is None:
            return
        try:
            self.occlusion_monitor.stop()
        finally:
            self.occlusion_monitor = None

    def poll_monitor_events(self):
        while True:
            try:
                event = self.monitor_events.get_nowait()
            except queue.Empty:
                break

            if event.kind in (
                'started',
                'stopped',
                'paused',
                'resumed',
                'raised',
                'error',
            ):
                self.status_var.set(format_monitor_event(event))

        self.root.after(250, self.poll_monitor_events)

    def exit_app(self):
        if self._exiting:
            return
        self._exiting = True
        if self._opacity_save_id is not None:
            try:
                self.root.after_cancel(self._opacity_save_id)
            except tk.TclError:
                pass
            self.save_opacity_setting()
        if self.theme_follower is not None:
            self.theme_follower.stop()
            self.theme_follower = None
        if self.tray_controller is not None:
            self.tray_controller.stop()
            self.tray_controller = None
        self.stop_auto_monitor()
        self.stop_ctrl_move_monitor()
        self.root.destroy()

    def is_normal_window(self, hwnd):
        """判断窗口是否是普通可见窗口"""
        if not win32gui.IsWindowVisible(hwnd):
            return False
        if win32gui.IsIconic(hwnd):  # 最小化
            return False
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return False

        # 获取窗口尺寸
        try:
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]

            # 忽略长或宽小于200像素的窗体
            if width < 200 or height < 200:
                return False

            # 获取屏幕尺寸
            import win32api
            screen_width = win32api.GetSystemMetrics(0)  # SM_CXSCREEN
            screen_height = win32api.GetSystemMetrics(1)  # SM_CYSCREEN

            # 打印调试信息
            print(f"窗口: {title}, 尺寸: {width}x{height}, 屏幕: {screen_width}x{screen_height}")

            # 忽略全屏窗体：窗口尺寸接近或等于屏幕尺寸
            # 使用更宽松的判断：宽度和高度都大于屏幕的95%
            if width > screen_width * 0.95 and height > screen_height * 0.95:
                print(f"  -> 被过滤（全屏）")
                return False

        except Exception as e:
            print(f"异常: {e}")
            return False

        return True

    def refresh_monitor_list(self):
        """刷新虚拟桌面列表"""
        self.virtual_desktops = []
        desktop_names = ['全部桌面']
        self.virtual_desktops.append(None)  # 全部桌面对应的 GUID 为 None

        try:
            if PYVDA_AVAILABLE:
                print("使用 pyvda 库获取虚拟桌面")
                # 使用 pyvda 获取所有虚拟桌面
                desktops = get_virtual_desktops()

                # 为每个虚拟桌面创建一个条目
                desktop_names.clear()
                desktop_names.append('全部桌面')
                self.virtual_desktops = [None]

                for idx, desktop in enumerate(desktops, 1):
                    # 获取桌面名称（pyvda 会自动编号）
                    desktop_name = f"桌面 {idx}"
                    desktop_names.append(desktop_name)
                    # 存储 GUID 字符串（确保格式一致）
                    guid_str = str(desktop.id).upper()
                    self.virtual_desktops.append(guid_str)
                    print(f"发现虚拟桌面: {desktop_name}, GUID: {guid_str}")

                print(f"总共发现 {len(desktops)} 个虚拟桌面")

            else:
                print("pyvda 不可用，尝试使用 Windows API")
                # 回退到之前的实现
                pythoncom.CoInitialize()

                # 枚举所有窗口并收集它们的虚拟桌面 ID
                desktop_guids = []
                desktop_window_counts = {}

                def collect_desktops(hwnd, extra):
                    if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                        try:
                            desktop_id = self.get_window_desktop_id(hwnd)
                            if desktop_id:
                                if desktop_id not in desktop_guids:
                                    desktop_guids.append(desktop_id)
                                    desktop_window_counts[desktop_id] = 0
                                desktop_window_counts[desktop_id] += 1
                        except Exception as e:
                            print(f"获取窗口桌面ID失败: {e}")
                    return True

                win32gui.EnumWindows(collect_desktops, None)

                # 为每个虚拟桌面创建一个条目
                desktop_names.clear()
                desktop_names.append('全部桌面')
                self.virtual_desktops = [None]

                for idx, guid in enumerate(desktop_guids, 1):
                    window_count = desktop_window_counts.get(guid, 0)
                    desktop_name = f"桌面 {idx} ({window_count} 个窗口)"
                    desktop_names.append(desktop_name)
                    self.virtual_desktops.append(guid)
                    print(f"发现虚拟桌面: {desktop_name}, ID: {guid}")

                print(f"总共发现 {len(desktop_guids)} 个虚拟桌面")

        except Exception as e:
            print(f"获取虚拟桌面失败，使用物理显示器: {e}")
            import traceback
            traceback.print_exc()
            self._enumerate_physical_monitors(desktop_names)

        # 更新下拉框
        try:
            self.desktop_combobox['values'] = desktop_names
            print(f"检测到 {len(self.virtual_desktops) - 1} 个桌面，已更新下拉框")
        except Exception as e:
            print(f"更新下拉框失败: {e}")

    def _enumerate_physical_monitors(self, desktop_names):
        """枚举物理显示器（备选方案）"""
        self.virtual_desktops = []
        desktop_names.clear()
        desktop_names.append('全部桌面')
        self.virtual_desktops.append(None)

        def callback(hmonitor, hdc, rect, data):
            try:
                monitor_info = win32api.GetMonitorInfo(hmonitor)
                work_area = monitor_info.get('Work')
                if work_area:
                    left, top, right, bottom = work_area
                    width = right - left
                    height = bottom - top

                    monitor_name = f"显示器 {len(self.virtual_desktops)} ({width}x{height})"
                    desktop_names.append(monitor_name)
                    self.virtual_desktops.append(hmonitor)

                    print(f"发现监视器: {monitor_name}")
            except Exception as e:
                print(f"获取监视器信息失败: {e}")
            return True

        try:
            win32api.EnumDisplayMonitors(None, None, callback, 0)
        except Exception as e:
            print(f"枚举监视器失败: {e}")

    def get_window_desktop_id(self, hwnd):
        """获取窗口所属的虚拟桌面 ID"""
        try:
            if PYVDA_AVAILABLE:
                # 使用 pyvda 获取窗口的虚拟桌面
                # 通过检查每个桌面，看窗口是否在其中
                from pyvda import get_virtual_desktops, VirtualDesktop

                desktops = get_virtual_desktops()

                # 获取窗口在当前桌面的状态
                try:
                    # 获取当前桌面
                    current = VirtualDesktop.current()

                    # 检查窗口是否在当前桌面可见
                    # 这是一个简化的方法
                    is_on_current = win32gui.IsWindowVisible(hwnd)

                    # 尝试使用 pyvda 的 AppView
                    from pyvda import AppView
                    appview = AppView(hwnd)
                    desktop = appview.desktop
                    return str(desktop.id).upper()

                except Exception as e:
                    # 如果 AppView 失败，返回 None
                    print(f"AppView 失败: {e}")
                    return None
            else:
                return None

        except Exception as e:
            print(f"获取窗口虚拟桌面失败: {e}")
            return None

    def on_desktop_changed(self, event=None):
        """当桌面选择改变时"""
        selected = self.desktop_var.get()
        print(f"桌面选择改变: {selected}")
        # 刷新窗口列表
        self.refresh_window_list()

    def get_window_icon(self, exe_path, size=(20, 20)):
        """从可执行文件获取图标，保留颜色位图的原始 alpha。"""
        try:
            if not exe_path:
                return None

            palette = get_theme_palette(self.root)
            foreground_rgb = hex_to_rgb(palette["text"])
            large_icons, small_icons = win32gui.ExtractIconEx(
                exe_path,
                0,
            )
            if not large_icons:
                return None

            hicon = large_icons[0]
            hbm_mask = None
            hbm_color = None
            try:
                _, _, _, hbm_mask, hbm_color = win32gui.GetIconInfo(hicon)
                if not hbm_color:
                    return None

                bitmap = win32gui.GetObject(hbm_color)
                width = bitmap.bmWidth
                height = abs(bitmap.bmHeight)

                from ctypes import wintypes

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", wintypes.DWORD),
                        ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD),
                    ]

                header = BITMAPINFOHEADER()
                header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                header.biWidth = width
                header.biHeight = -height
                header.biPlanes = 1
                header.biBitCount = 32
                header.biCompression = 0

                buffer = ctypes.create_string_buffer(width * height * 4)
                hdc = win32gui.GetDC(0)
                try:
                    result = ctypes.windll.gdi32.GetDIBits(
                        ctypes.c_void_p(int(hdc)),
                        ctypes.c_void_p(int(hbm_color)),
                        0,
                        height,
                        buffer,
                        ctypes.byref(header),
                        0,
                    )
                finally:
                    win32gui.ReleaseDC(0, hdc)

                if not result:
                    return None

                icon_image = Image.frombuffer(
                    'RGBA',
                    (width, height),
                    buffer.raw,
                    'raw',
                    'BGRA',
                    0,
                    1,
                )
                if is_monochrome_icon(icon_image):
                    alpha = icon_image.getchannel("A")
                    icon_image = Image.new(
                        "RGBA",
                        icon_image.size,
                        foreground_rgb + (0,),
                    )
                    icon_image.putalpha(alpha)
                icon_image = icon_image.resize(
                    size,
                    Image.Resampling.LANCZOS,
                )
                return ImageTk.PhotoImage(icon_image)
            finally:
                if hbm_mask:
                    win32gui.DeleteObject(hbm_mask)
                if hbm_color:
                    win32gui.DeleteObject(hbm_color)
                for icon in (large_icons or []) + (small_icons or []):
                    win32gui.DestroyIcon(icon)
        except Exception as e:
            print(f"获取图标失败 ({exe_path}): {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_window_process_info(self, hwnd):
        """获取窗口的进程信息"""
        try:
            # 获取进程ID
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            # 获取命令行参数
            cmdline = ""
            exe_path = None
            try:
                process = psutil.Process(pid)
                exe_path = process.exe()
                cmdline = process.cmdline()
                if cmdline:
                    cmdline = " ".join(cmdline)
                else:
                    cmdline = f"PID: {pid}"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cmdline = f"PID: {pid} (无法获取详细信息)"

            # 获取图标
            icon = self.get_window_icon(exe_path)

            return {
                'pid': pid,
                'cmdline': cmdline,
                'exe_path': exe_path,
                'icon': icon
            }
        except Exception as e:
            print(f"获取进程信息失败: {e}")
            return {
                'pid': 0,
                'cmdline': '未知',
                'exe_path': None,
                'icon': None
            }

    def get_visible_windows(self):
        """获取所有可见窗口"""
        hwnd_list = []

        # 获取当前选择的桌面索引
        selected_desktop = self.desktop_var.get()
        desktop_index = -1  # -1 表示全部桌面

        if selected_desktop != '全部桌面':
            # 从下拉框中获取索引
            values = self.desktop_combobox['values']
            try:
                desktop_index = values.index(selected_desktop) - 1  # 减1因为第一个是"全部桌面"
                selected_desktop_obj = self.virtual_desktops[desktop_index + 1]

                # 获取选择桌面的 GUID（现在是字符串格式）
                selected_desktop_guid = selected_desktop_obj
                if isinstance(selected_desktop_guid, str):
                    selected_desktop_guid = selected_desktop_guid.upper()
                print(f"过滤虚拟桌面索引: {desktop_index}, GUID: {selected_desktop_guid}")
            except (ValueError, IndexError) as e:
                print(f"获取桌面索引失败: {e}")
                desktop_index = -1
                selected_desktop_obj = None
                selected_desktop_guid = None
        else:
            selected_desktop_obj = None
            selected_desktop_guid = None

        def callback(hwnd, extra):
            if is_current_process_window(hwnd):
                return True
            if self.is_normal_window(hwnd):
                # 如果选择了特定虚拟桌面，检查窗口是否在该桌面上
                if desktop_index >= 0 and selected_desktop_guid:
                    window_desktop_id = self.get_window_desktop_id(hwnd)

                    if window_desktop_id is None:
                        return True

                    # 比较虚拟桌面 GUID（统一使用大写格式）
                    if window_desktop_id.upper() == selected_desktop_guid.upper():
                        hwnd_list.append(hwnd)
                        print(f"✓ 窗口 {win32gui.GetWindowText(hwnd)} 在选定桌面上 (GUID: {window_desktop_id})")
                    else:
                        print(f"✗ 窗口 {win32gui.GetWindowText(hwnd)} 不在选定桌面上 (窗口GUID: {window_desktop_id}, 选中GUID: {selected_desktop_guid})")
                else:
                    # 全部桌面，都添加
                    hwnd_list.append(hwnd)
            return True

        win32gui.EnumWindows(callback, None)
        return hwnd_list

    def refresh_window_list(self):
        """刷新窗口列表"""
        # 保存之前选中的窗口句柄
        previously_selected = set()
        for hwnd, title, var in self.window_list:
            if var.get():
                previously_selected.add(hwnd)

        canvas = self.window_list_canvas
        canvas.delete('window_row')
        self.window_rows.clear()
        self.window_list_icons.clear()
        self.window_list.clear()

        hwnds = self.get_visible_windows()
        palette = get_theme_palette(self.root)
        normal_font = tkfont.Font(
            root=self.root,
            family=FONT_FAMILY,
            size=10,
        )
        accent_font = tkfont.Font(
            root=self.root,
            family=FONT_FAMILY,
            size=10,
            weight='bold',
        )
        canvas_width = max(canvas.winfo_width(), 1)

        for row_index, hwnd in enumerate(hwnds):
            title = win32gui.GetWindowText(hwnd)
            # 获取窗口尺寸
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]

            # 获取进程信息
            proc_info = self.get_window_process_info(hwnd)
            pid = proc_info['pid']
            cmdline = proc_info['cmdline']
            exe_path = proc_info.get('exe_path', '')
            icon = proc_info.get('icon')

            # 默认不选中，但如果是之前选中的则保持选中
            was_selected = hwnd in previously_selected
            var = tk.BooleanVar(value=was_selected)
            self.window_list.append((hwnd, title, var))

            row_tag = 'window_row_{0}'.format(hwnd)
            tags = (row_tag, 'window_row')
            y = (
                WINDOW_LIST_TOP_PADDING
                + row_index * WINDOW_LIST_ROW_HEIGHT
                + WINDOW_LIST_ROW_HEIGHT / 2
            )
            checkbox_left = 12
            checkbox_top = y - 7
            checkbox_right = checkbox_left + 14
            checkbox_bottom = y + 7
            checkbox_rect = canvas.create_rectangle(
                checkbox_left,
                checkbox_top,
                checkbox_right,
                checkbox_bottom,
                fill='',
                outline=palette['muted'],
                width=1,
                tags=tags,
            )
            checkmark = canvas.create_line(
                checkbox_left + 3,
                y,
                checkbox_left + 6,
                y + 3,
                checkbox_right - 2,
                y - 4,
                fill=palette['accent_text'],
                width=2,
                state=tk.HIDDEN,
                tags=tags,
            )

            # 显示图标（如果有）
            text_x = 34
            if icon:
                canvas.create_image(
                    32,
                    y,
                    anchor=tk.W,
                    image=icon,
                    tags=tags,
                )
                self.window_list_icons[hwnd] = icon
                text_x = 58

            # 添加进程名（蓝色）放在图标旁边
            current_x = text_x
            if exe_path:
                import os
                exe_name = os.path.basename(exe_path)
                canvas.create_text(
                    current_x,
                    y,
                    text=exe_name,
                    font=accent_font,
                    fill=palette['accent'],
                    anchor=tk.W,
                    tags=tags,
                )
                current_x += accent_font.measure(exe_name) + 6

            # 构建基础信息文本
            base_text = f"{title} ({width}x{height}) | [{hwnd}] | PID: {pid}"

            # 基础信息标签
            canvas.create_text(
                current_x,
                y,
                text=base_text,
                font=normal_font,
                fill=palette['text'],
                anchor=tk.W,
                tags=tags,
            )
            current_x += normal_font.measure(base_text)

            # 添加命令行（简短版本）
            if (
                cmdline
                and len(cmdline) <= 80
                and current_x < canvas_width - 12
            ):
                canvas.create_text(
                    current_x,
                    y,
                    text=f" | {cmdline}",
                    font=normal_font,
                    fill=palette['muted'],
                    anchor=tk.W,
                    tags=tags,
                )

            self.window_rows[hwnd] = {
                'var': var,
                'checkbox': checkbox_rect,
                'checkmark': checkmark,
            }
            canvas.tag_bind(
                row_tag,
                '<Button-1>',
                lambda event, selected_hwnd=hwnd: (
                    self.toggle_window_selection(selected_hwnd)
                ),
            )
            self.update_window_checkbox(hwnd)

        self._window_list_content_height = max(
            1,
            (
                WINDOW_LIST_TOP_PADDING * 2
                + len(hwnds) * WINDOW_LIST_ROW_HEIGHT
            ),
        )
        self.update_window_list_scrollregion()

        self.status_var.set(f'找到 {len(hwnds)} 个可见窗口')

    def toggle_window_selection(self, hwnd):
        """Toggle one Canvas-rendered window row."""
        row = self.window_rows.get(hwnd)
        if row is None:
            return
        row['var'].set(not row['var'].get())
        self.update_window_checkbox(hwnd)

    def update_window_checkbox(self, hwnd):
        """Refresh the Canvas checkbox for one window."""
        row = self.window_rows.get(hwnd)
        if row is None:
            return
        palette = get_theme_palette(self.root)
        checked = bool(row['var'].get())
        self.window_list_canvas.itemconfigure(
            row['checkbox'],
            fill=palette['accent'] if checked else '',
            outline=palette['accent'] if checked else palette['muted'],
        )
        self.window_list_canvas.itemconfigure(
            row['checkmark'],
            state=tk.NORMAL if checked else tk.HIDDEN,
        )

    def update_all_window_checkboxes(self):
        for hwnd in self.window_rows:
            self.update_window_checkbox(hwnd)

    def invert_selection(self):
        """反转所有窗口的选中状态"""
        for hwnd, title, var in self.window_list:
            var.set(not var.get())
        self.update_all_window_checkboxes()

    def deselect_all(self):
        """全不选所有窗口"""
        for hwnd, title, var in self.window_list:
            var.set(False)
        self.update_all_window_checkboxes()

    def arrange_selected_windows(self):
        """排列选中的窗口"""
        # 获取选中的窗口
        selected_hwnds = []
        for hwnd, title, var in self.window_list:
            if var.get():
                selected_hwnds.append(hwnd)

        if not selected_hwnds:
            messagebox.showwarning('提示', '请至少选择一个窗口！')
            return

        # 获取偏移量
        offset = self.offset_var.get()
        count = len(selected_hwnds)

        # 确认对话框
        reply = messagebox.askyesno(
            '确认操作',
            f'即将排列 {count} 个窗口，偏移量为 {offset} 像素。\n确定要继续吗？'
        )

        if not reply:
            return

        # 执行窗口排列
        try:
            self.status_var.set(f'正在排列 {count} 个窗口...')
            self.root.update()

            # 计算起始位置
            x = 0
            y = count * offset

            for idx, hwnd in enumerate(selected_hwnds):
                # 获取窗口原始大小
                rect = win32gui.GetWindowRect(hwnd)
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]

                # 移动窗口
                win32gui.MoveWindow(hwnd, x, y, width, height, True)

                # 更新下一个窗口位置
                x += offset
                y -= offset

            self.status_var.set(f'成功排列 {count} 个窗口')
            messagebox.showinfo('完成', f'已成功排列 {count} 个窗口！')

        except Exception as e:
            messagebox.showerror('错误', f'排列窗口时发生错误：\n{str(e)}')
            self.status_var.set('排列失败')


def main():
    enable_dpi_awareness()
    startup_mode = startup_requested()
    set_windows_app_id()
    startup_manager = StartupManager()
    if getattr(sys, "frozen", False):
        try:
            startup_manager.sync_path()
        except Exception as exc:
            print("更新自动启动路径失败: {0}".format(exc))

    instance_guard = SingleInstanceGuard()
    if not instance_guard.acquire():
        if not startup_mode:
            show_single_instance_message()
        return

    try:
        root = tk.Tk()
        configure_tk_scaling(root)
        if startup_mode:
            root.withdraw()
        WindowArranger(
            root,
            startup_mode=startup_mode,
            startup_manager=startup_manager,
        )
        root.mainloop()
    finally:
        instance_guard.release()


if __name__ == '__main__':
    main()
