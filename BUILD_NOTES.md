# 打包说明

## 方法一：使用 build_exe.bat（推荐）

直接双击运行 `build_exe.bat` 即可自动打包。

## 方法二：手动打包

### 1. 安装依赖

```bash
pip install -r requirements.txt pyinstaller
```

### 2. 使用命令行打包

```bash
pyinstaller --onefile --windowed --name arrange_windows_gui --icon assets\arrwin.ico --add-data "assets;assets" --clean --hidden-import=pyvda --hidden-import=comtypes --hidden-import=pythoncom --hidden-import=pystray --collect-all pyvda --collect-all comtypes --collect-all pystray --collect-all sv_ttk arrange_windows_gui.py
```

### 3. 或使用 spec 文件打包

```bash
pyinstaller arrange_windows_gui.spec
```

## 依赖说明

程序依赖以下库：
- **pyvda**: Windows 虚拟桌面管理（新增）
- **comtypes**: COM 组件支持（新增）
- **pythoncom**: Windows COM 接口（已有）
- **pywin32**: Windows API（已有）
- **PIL/Pillow**: 图像处理（已有）
- **psutil**: 进程信息（已有）
- **pystray**: Windows 系统托盘集成
- **sv-ttk**: Windows 11 Sun Valley 风格 ttk 主题，需收集包内的 `sv.tcl` 资源
- **pywinstyles**: Windows 标题栏明暗配色与 DWM 样式适配
- **darkdetect**: 读取 Windows 应用明暗模式
- **tkinter**: GUI 界面（Python 内置）

## 打包选项说明

- `--onefile`: 打包成单个 exe 文件
- `--windowed`: 不显示控制台窗口
- `--hidden-import`: 显式导入可能被遗漏的模块
- `--collect-all`: 收集某个包的所有数据

## 输出

打包成功后，exe 文件位于：
```
dist\arrange_windows_gui.exe
```

## 注意事项

1. 首次运行可能需要管理员权限
2. 确保系统已安装 VC++ 运行时
3. pyvda 需要 Windows 10/11 的虚拟桌面功能
4. 杀毒软件可能会误报，需要添加信任
5. `sv-ttk 2.6.0` 要求 Python 3.8 或更高版本；升级到 Python 3.9+ 后可单独评估更新版本

## 常见问题

### 1. 打包后提示缺少模块

在 `build_exe.bat` 或 `spec` 文件中添加对应的 `--hidden-import`

### 2. 虚拟桌面功能不工作

确保 pyvda 已正确安装和打包
```bash
pip install pyvda
```

### 3. exe 文件过大

可以尝试添加 `--exclude-module` 排除不需要的模块
