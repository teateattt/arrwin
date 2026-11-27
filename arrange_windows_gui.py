import tkinter as tk
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
    apply_app_theme,
    apply_native_window_style,
    configure_tk_scaling,
    enable_dpi_awareness,
    get_theme_palette,
    get_switch_checkbutton_style,
    hex_to_rgb,
)
try:
    from pyvda import VirtualDesktop, get_virtual_desktops
    PYVDA_AVAILABLE = True
except ImportError:
    PYVDA_AVAILABLE = False
    print("pyvda 库不可用，将使用物理显示器")


STARTUP_INITIALIZATION_DELAY_MS = 20_000


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
        self.config_manager = ConfigManager()  # 初始化配置管理器
        self.startup_mode = startup_mode
        self.startup_manager = startup_manager or StartupManager()
        self.theme_mode = theme_mode
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

        # 绑定窗口大小改变事件
        self.root.bind('<Configure>', self.on_window_configure)
        # 绑定鼠标释放事件
        self.root.bind('<ButtonRelease-1>', self.on_mouse_release)
        self.root.bind('<Unmap>', self.on_window_unmap)

        self.setup_ui()
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
            self._in_tray = False
            self.root.deiconify()
            if self._last_window_state == 'zoomed':
                self.root.state('zoomed')
            self.root.lift()
            self.root.focus_force()
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

    def setup_ui(self):
        """初始化界面"""
        self.theme_mode = apply_app_theme(self.root, self.theme_mode)
        palette = get_theme_palette(self.root)
        switch_style = get_switch_checkbutton_style(self.root)

        self.root.title(APP_TITLE)
        try:
            self.root.iconbitmap(resource_path('assets', 'arrwin.ico'))
        except tk.TclError:
            pass

        # 从配置文件加载窗口尺寸
        width = self.config_manager.get_window_width()
        height = self.config_manager.get_window_height()
        self.root.geometry(f'{width}x{height}')
        self.root.resizable(True, True)

        # 主框架
        main_frame = ttk.Frame(self.root, padding="12")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)

        # 标题
        title_label = ttk.Label(
            main_frame,
            text='Windows 11 窗口排列工具',
            font=(FONT_FAMILY, 15, 'bold')
        )
        title_label.grid(row=0, column=0, pady=(0, 12))

        # 设置区域
        settings_frame = ttk.LabelFrame(main_frame, text='排列设置', padding="12")
        settings_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        settings_frame.columnconfigure(1, weight=1)

        # 偏移量设置
        ttk.Label(settings_frame, text='窗口偏移量 (像素):').grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
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
            font=(FONT_FAMILY, 9),
            style='Muted.TLabel',
        )
        info_label.grid(row=0, column=2, sticky=tk.W)

        # 桌面选择下拉框（新添加）
        ttk.Label(settings_frame, text='选择桌面:').grid(row=1, column=0, sticky=tk.W, padx=(0, 10), pady=(10, 0))
        self.desktop_combobox = ttk.Combobox(
            settings_frame,
            textvariable=self.desktop_var,
            state='readonly',
            width=20
        )
        self.desktop_combobox.grid(row=1, column=1, sticky=tk.W, padx=(0, 20), pady=(10, 0))
        # 绑定选择变化事件
        self.desktop_combobox.bind('<<ComboboxSelected>>', self.on_desktop_changed)

        # 自动显示被完全遮挡的窗口
        ttk.Label(settings_frame, text='遮挡监控:').grid(
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
            command=self.on_overlap_nudge_changed
        )
        self.overlap_nudge_check.grid(
            row=2,
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
            command=self.on_auto_exclude_self_changed
        )
        self.auto_raise_exclude_self_check.grid(
            row=3,
            column=1,
            columnspan=2,
            sticky=tk.W,
            padx=(0, 20),
            pady=(10, 0)
        )
        self.update_auto_option_states()

        ttk.Label(settings_frame, text='Ctrl 悬停提升:').grid(
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

        ttk.Label(settings_frame, text='悬停触发:').grid(
            row=5,
            column=0,
            sticky=tk.W,
            padx=(0, 10),
            pady=(10, 0)
        )
        self.ctrl_move_required_var = tk.BooleanVar(
            value=self.config_manager.get_ctrl_move_required()
        )
        self.ctrl_move_required_check = ttk.Checkbutton(
            settings_frame,
            text='必须移动鼠标后触发',
            variable=self.ctrl_move_required_var,
            command=self.on_ctrl_move_required_changed
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
            command=self.on_ctrl_move_activate_changed
        )
        self.ctrl_move_raise_activate_check.grid(
            row=5,
            column=2,
            sticky=tk.W,
            pady=(10, 0)
        )

        ttk.Label(settings_frame, text='悬停过滤:').grid(
            row=6,
            column=0,
            sticky=tk.W,
            padx=(0, 10),
            pady=(10, 0)
        )
        self.ctrl_move_raise_exclude_self_var = tk.BooleanVar(
            value=self.config_manager.get_ctrl_move_raise_exclude_self()
        )
        self.ctrl_move_raise_exclude_self_check = ttk.Checkbutton(
            settings_frame,
            text='排除本程序自身窗口',
            variable=self.ctrl_move_raise_exclude_self_var,
            command=self.on_ctrl_exclude_self_changed
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

        ttk.Label(settings_frame, text='系统启动:').grid(
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

        # 窗口列表区域
        list_frame = ttk.LabelFrame(main_frame, text='可见窗口列表', padding="12")
        list_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)

        # 按钮区域
        button_frame = ttk.Frame(list_frame)
        button_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        button_frame.columnconfigure(0, weight=1)

        ttk.Button(button_frame, text='全选', command=self.select_all, width=10).grid(row=0, column=0, padx=(0, 5))
        ttk.Button(button_frame, text='全不选', command=self.deselect_all, width=10).grid(row=0, column=1, padx=5)
        ttk.Button(button_frame, text='刷新列表', command=self.refresh_window_list, width=10).grid(row=0, column=2, padx=5)

        # 窗口列表容器（带滚动条）
        list_container = ttk.Frame(list_frame, style='List.TFrame')
        list_container.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)

        # 创建 Canvas 和 Scrollbar
        canvas = tk.Canvas(
            list_container,
            highlightthickness=0,
            borderwidth=0,
            bg=palette['surface_alt'],
        )
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas, style='List.TFrame')

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)

        # 鼠标滚轮支持
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # 操作按钮区域
        action_frame = ttk.Frame(main_frame)
        action_frame.grid(row=3, column=0, pady=(0, 10))

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
        ttk.Button(action_frame, text='退出', command=self.exit_app, width=10).grid(row=0, column=1)

        # 状态栏
        self.status_var = tk.StringVar(value='就绪')
        self.status_bar = ttk.Label(
            main_frame,
            textvariable=self.status_var,
            style='Status.TLabel',
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=max(300, width - 40),
        )
        self.status_bar.grid(row=4, column=0, sticky=(tk.W, tk.E))

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

    def get_window_icon(self, exe_path, size=(16, 16)):
        """从可执行文件获取图标"""
        try:
            if not exe_path:
                return None

            import os
            import tempfile

            # 使用 PIL 从 exe 文件提取图标
            from PIL import Image

            background_rgb = hex_to_rgb(
                get_theme_palette(self.root)["surface"]
            )

            # 尝试直接从 exe 文件加载图标
            # 使用 ExtractIconEx 获取图标句柄
            icon_list = win32gui.ExtractIconEx(exe_path, 0)
            if not icon_list or not icon_list[0]:
                return None

            hicon = icon_list[0][0]
            print(f"提取到图标: {exe_path}")

            # 创建设备上下文和位图
            hdc = win32gui.GetDC(0)
            hdc_mem = win32gui.CreateCompatibleDC(hdc)
            hbitmap = win32gui.CreateCompatibleBitmap(hdc, size[0], size[1])
            hbitmap_old = win32gui.SelectObject(hdc_mem, hbitmap)

            # 使用当前主题窗口行的背景色填充透明图标区域
            import ctypes
            background_brush = ctypes.windll.gdi32.CreateSolidBrush(
                win32api.RGB(*background_rgb)
            )
            previous_brush = win32gui.SelectObject(
                hdc_mem,
                background_brush,
            )
            try:
                ctypes.windll.gdi32.Rectangle(
                    hdc_mem,
                    0,
                    0,
                    size[0],
                    size[1],
                )
            finally:
                win32gui.SelectObject(hdc_mem, previous_brush)
                win32gui.DeleteObject(background_brush)

            # 绘制图标
            win32gui.DrawIconEx(hdc_mem, 0, 0, hicon, size[0], size[1], 0, None, 0x0003)

            # 背景已经由 DrawIconEx 处理，但我们稍后在 PIL 中设置背景色

            # 恢复旧位图
            win32gui.SelectObject(hdc_mem, hbitmap_old)

            # 创建临时文件保存位图
            temp_file = tempfile.NamedTemporaryFile(suffix='.bmp', delete=False)
            temp_path = temp_file.name
            temp_file.close()

            try:
                # 使用 GetDIBits 获取位图数据
                import ctypes
                from ctypes import wintypes

                # 获取位图信息
                bmp_info = win32gui.GetObject(hbitmap)

                # 创建位图信息头
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

                bmi_header = BITMAPINFOHEADER()
                bmi_header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi_header.biWidth = bmp_info.bmWidth
                bmi_header.biHeight = -bmp_info.bmHeight  # 负值表示顶部向下
                bmi_header.biPlanes = 1
                bmi_header.biBitCount = 32  # RGBA
                bmi_header.biCompression = 0  # BI_RGB
                bmi_header.biSizeImage = 0
                bmi_header.biXPelsPerMeter = 0
                bmi_header.biYPelsPerMeter = 0
                bmi_header.biClrUsed = 0
                bmi_header.biClrImportant = 0

                # 分配缓冲区
                buffer_size = bmp_info.bmWidth * abs(bmp_info.bmHeight) * 4
                buffer = ctypes.create_string_buffer(buffer_size)

                # 使用 GetDIBits 获取位图数据
                # 将 PyHANDLE 转换为整数，再转换为指针
                result = ctypes.windll.gdi32.GetDIBits(
                    ctypes.c_void_p(int(hdc_mem)),  # HDC
                    ctypes.c_void_p(int(hbitmap)),  # HBITMAP
                    0,  # 起始扫描线
                    abs(bmp_info.bmHeight),  # 扫描线数量
                    buffer,  # 输出缓冲区
                    ctypes.byref(bmi_header),  # 位图信息
                    0  # DIB_RGB_COLORS
                )

                if result:
                    # 创建 PIL 图像
                    img = Image.frombuffer('RGBA', (bmp_info.bmWidth, abs(bmp_info.bmHeight)),
                                         buffer.raw, 'raw', 'BGRA', 0, 1)

                    # 创建与当前主题一致的背景图像
                    background = Image.new(
                        "RGBA",
                        img.size,
                        background_rgb + (255,),
                    )
                    background.paste(img, mask=img.split()[3])  # 使用 alpha 通道
                    img = background

                    print(f"成功创建图标: {exe_path}")
                else:
                    print(f"GetDIBits 失败，使用占位符")
                    img = Image.new(
                        "RGBA",
                        size,
                        background_rgb + (255,),
                    )

                # 清理资源
                win32gui.DeleteObject(hbitmap)
                win32gui.DeleteDC(hdc_mem)
                win32gui.ReleaseDC(0, hdc)
                win32gui.DestroyIcon(hicon)

                return ImageTk.PhotoImage(img)

            finally:
                # 删除临时文件
                try:
                    os.unlink(temp_path)
                except:
                    pass

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

        # 清空列表
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.window_list.clear()

        hwnds = self.get_visible_windows()

        for hwnd in hwnds:
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

            # 创建主框架
            main_frame = ttk.Frame(
                self.scrollable_frame,
                style='WindowRow.TFrame',
            )
            main_frame.pack(fill=tk.X, pady=2, padx=2)

            # 创建单行显示的框架
            content_frame = ttk.Frame(main_frame, style='WindowRow.TFrame')
            content_frame.pack(fill=tk.X, padx=5, pady=3)

            # 复选框
            checkbox = ttk.Checkbutton(
                content_frame,
                variable=var
            )
            checkbox.pack(side=tk.LEFT, padx=(0, 5))

            # 显示图标（如果有）
            if icon:
                icon_label = ttk.Label(content_frame, image=icon)
                icon_label.pack(side=tk.LEFT, padx=(0, 5))
                # 保持对图标的引用，防止被垃圾回收
                icon_label.image = icon

            # 添加进程名（蓝色）放在图标旁边
            if exe_path:
                import os
                exe_name = os.path.basename(exe_path)
                exe_label = ttk.Label(
                    content_frame,
                    text=exe_name,
                    font=(FONT_FAMILY, 8),
                    style='Accent.TLabel',
                    anchor=tk.W,
                )
                exe_label.pack(side=tk.LEFT, padx=(0, 5))

            # 构建基础信息文本
            base_text = f"{title} ({width}x{height}) | [{hwnd}] | PID: {pid}"

            # 基础信息标签
            base_label = ttk.Label(
                content_frame,
                text=base_text,
                font=(FONT_FAMILY, 8),
                anchor=tk.W,
            )
            base_label.pack(side=tk.LEFT)

            # 添加命令行（简短版本）
            if cmdline and len(cmdline) <= 80:
                cmdline_label = ttk.Label(
                    content_frame,
                    text=f" | {cmdline}",
                    font=(FONT_FAMILY, 8),
                    style='Muted.TLabel',
                    anchor=tk.W,
                )
                cmdline_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_var.set(f'找到 {len(hwnds)} 个可见窗口')

    def select_all(self):
        """全选所有窗口"""
        for hwnd, title, var in self.window_list:
            var.set(True)

    def deselect_all(self):
        """全不选所有窗口"""
        for hwnd, title, var in self.window_list:
            var.set(False)

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
