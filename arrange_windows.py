import win32gui
import win32con

# 判断窗口是否是普通可见窗口
def is_normal_window(hwnd):
    if not win32gui.IsWindowVisible(hwnd):
        return False
    if win32gui.IsIconic(hwnd):  # 最小化
        return False
    title = win32gui.GetWindowText(hwnd)
    if not title:
        return False
    return True

# 获取所有可见窗口
def get_visible_windows():
    hwnd_list = []

    def callback(hwnd, extra):
        if is_normal_window(hwnd):
            hwnd_list.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return hwnd_list

# 按规则移动窗口
def arrange_windows():
    hwnds = get_visible_windows()
    count = len(hwnds)

    if count == 0:
        print("没有可移动的窗口")
        return

    print(f"检测到 {count} 个窗口，开始移动")

    # 第一个窗口位置
    x = 0
    y = count * 35

    for hwnd in hwnds:
        # 获取窗口原始大小
        rect = win32gui.GetWindowRect(hwnd)
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]

        # 移动窗口
        win32gui.MoveWindow(hwnd, x, y, width, height, True)

        print(f"窗口 {hwnd} 移动到 ({x}, {y})")

        # 下一个窗口位置
        x += 35
        y -= 35

# 直接执行功能
arrange_windows()
print("窗口排列完成，程序退出")
