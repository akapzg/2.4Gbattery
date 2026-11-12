# -*- coding: utf-8 -*-
"""
Windows 11 原生风格外设电量系统托盘中心（支持浅色/深色双主题 + Win11 原生电池图标）
支持设备：
1. 狼蛛 AULA F87 Pro 无线机械键盘 (3554:FA09 / 0xFF02 / 0x13 / 0x0A 被动状态帧监听)
2. 罗技 G PRO Wireless (GPW) 鼠标 (046D:C547 / HID++ 2.0 0x1004 / 0x1000)
3. HyperX Cloud II Core Wireless 耳机 (03F0:0995 / 0xFF13 / 0x66 / 0x89)
"""

import ctypes
from ctypes import wintypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
import winreg
import hid
from PIL import Image, ImageDraw, ImageFont, ImageTk
import pystray

# 开启高分屏 DPI 感知
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_NAME = "PeripheralBatteryCenter"
APP_VERSION = "v1.0.1"
GITHUB_REPO = "akapzg/2.4Gbattery"
EXIT_EVENT_NAME = r"Local\PeripheralBatteryCenter_ExitEvent"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
PERSONALIZE_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
CACHE_FILE = os.path.join(get_base_dir(), "battery_cache.json")
FLUENT_ICON_FONT_PATH = "C:/Windows/Fonts/SegoeIcons.ttf"


def kill_previous_instances():
    """
    单实例接管：
    1) 先触发命名退出事件通知支持优雅退出的旧实例主动移除托盘图标并退出；
    2) 再通过 Win32 Toolhelp32 快照扫描并终止残留的旧版 2.4Gbattery*.exe 进程
       （排除当前进程 os.getpid() 与 PyInstaller 父引导进程 os.getppid()）。
    """
    kernel32 = ctypes.windll.kernel32
    EVENT_MODIFY_STATE = 0x0002
    h_evt = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, EXIT_EVENT_NAME)
    if h_evt:
        kernel32.SetEvent(h_evt)
        kernel32.CloseHandle(h_evt)
        time.sleep(0.25)

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_TERMINATE = 0x0001

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    my_pids = {os.getpid(), os.getppid()}
    h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h_snap == wintypes.HANDLE(-1).value:
        return

    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
            while True:
                exe_name = pe.szExeFile.lower()
                pid = pe.th32ProcessID
                if exe_name.startswith("2.4gbattery") and exe_name.endswith(".exe") and pid not in my_pids:
                    h_proc = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                    if h_proc:
                        kernel32.TerminateProcess(h_proc, 0)
                        kernel32.CloseHandle(h_proc)
                if not kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(h_snap)


def load_battery_cache():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_battery_cache(cache_dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_dict, f, ensure_ascii=False)
    except Exception:
        pass


def get_win11_light_prefs():
    """读取 Windows 11 系统任务栏与应用浅色/深色偏好 (返回 taskbar_light, app_light)"""
    taskbar_light, app_light = True, True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY_PATH, 0, winreg.KEY_READ) as k:
            taskbar_light = bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
            app_light = bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except Exception:
        pass
    return taskbar_light, app_light


# ============================================================================
# 1. 只读硬件电量查询与监听模块
# ============================================================================

_f87_state = {
    "online": False,
    "battery": None,
    "charging": False,
    "status_text": "等待键盘推送电量帧...",
    "updated_at": None,
}
_f87_lock = threading.Lock()


def start_f87pro_listener(on_update_callback=None):
    """
    后台常驻监听狼蛛 F87 Pro 的 0xFF02 / Report ID 0x13 被动推送包 (13 0A ...):
    13 0A 01 00 | 04 05 <battery> <charging> | ... | <checksum>
    """
    cache = load_battery_cache()
    if "keyboard" in cache and cache["keyboard"].get("battery") is not None:
        with _f87_lock:
            _f87_state["battery"] = cache["keyboard"].get("battery")
            _f87_state["charging"] = cache["keyboard"].get("charging", False)
            _f87_state["online"] = True
            _f87_state["status_text"] = "已缓存 · 监听实时推送中"

    def _loop():
        while True:
            devs = [
                d for d in hid.enumerate(0x3554, 0xFA09)
                if d.get("usage_page") == 0xFF02
            ]
            if not devs:
                with _f87_lock:
                    _f87_state["online"] = False
                    _f87_state["status_text"] = "未连接 2.4G 接收器"
                time.sleep(3)
                continue

            h = hid.device()
            try:
                h.open_path(devs[0]["path"])
                h.set_nonblocking(0)
                with _f87_lock:
                    if _f87_state["battery"] is None:
                        _f87_state["status_text"] = "等待推送 (唤醒或插拔充电触发)"
                while True:
                    resp = h.read(64, timeout_ms=1000)
                    if not resp or len(resp) < 20 or resp[0] != 0x13:
                        continue
                    if (sum(resp[:19]) & 0xFF) != resp[19]:
                        continue
                    if resp[1] == 0x0A:
                        changed = False
                        with _f87_lock:
                            _f87_state["online"] = (resp[2] == 0x01)
                            if resp[4] == 0x04 and resp[5] == 0x05 and 0 <= resp[6] <= 100:
                                _f87_state["battery"] = resp[6]
                                _f87_state["charging"] = (resp[7] == 0x10)
                                _f87_state["online"] = True
                                _f87_state["status_text"] = "充电中" if resp[7] == 0x10 else "无线使用中"
                                _f87_state["updated_at"] = time.strftime("%H:%M:%S")
                                c = load_battery_cache()
                                c["keyboard"] = {
                                    "battery": _f87_state["battery"],
                                    "charging": _f87_state["charging"],
                                    "time": _f87_state["updated_at"],
                                }
                                save_battery_cache(c)
                                changed = True
                            elif resp[5] == 0x02:
                                _f87_state["online"] = (resp[6] == 0x01)
                                changed = True
                        if changed and on_update_callback:
                            on_update_callback()
            except Exception:
                time.sleep(2)
            finally:
                try:
                    h.close()
                except Exception:
                    pass

    threading.Thread(target=_loop, daemon=True).start()


def query_aula_f87pro():
    """
    通过 0xFF02 / Report ID 0x13 发送 0x4A 查询狼蛛 F87 Pro 实时电量与充电状态，
    同时兼容 0x0A 主动推送帧。
    实测报文：
    - 放电中: 13 4A 01 00 02 <battery> 01 ... <checksum>
    - 充电中: 13 4A 01 00 02 <battery> 10 ... <checksum>
    """
    result = {
        "id": "keyboard",
        "name": "狼蛛 F87 Pro",
        "Category": "键盘 · 2.4G",
        "online": False,
        "battery": None,
        "charging": False,
        "status_text": "未连接接收器",
    }
    devs = [
        d for d in hid.enumerate(0x3554, 0xFA09)
        if d.get("usage_page") == 0xFF02
    ]
    if not devs:
        return result

    h = hid.device()
    try:
        h.open_path(devs[0]["path"])
        h.set_nonblocking(1)
        # 先检查是否有键盘主动推送的 0x0A 帧
        latest_0a = None
        while True:
            r = h.read(64)
            if not r:
                break
            if len(r) >= 20 and r[0] == 0x13 and r[1] == 0x0A and r[4] == 0x04 and r[5] == 0x05:
                if (sum(r[:19]) & 0xFF) == r[19]:
                    latest_0a = r

        # 发送 0x4A 实时查询帧
        pkt = [0x13, 0x4A] + [0x00] * 17
        pkt.append(sum(pkt) & 0xFF)
        h.write(pkt)

        h.set_nonblocking(0)
        resp = h.read(64, timeout_ms=400)
        if not resp and latest_0a:
            resp = latest_0a

        if not resp or len(resp) < 20 or resp[0] != 0x13:
            result["status_text"] = "已休眠 / 离线"
            return result

        if (sum(resp[:19]) & 0xFF) != resp[19]:
            result["status_text"] = "校验异常"
            return result

        if resp[1] == 0x0A and resp[4] == 0x04 and resp[5] == 0x05:
            battery = resp[6]
            state_byte = resp[7]
        else:
            battery = resp[5]
            state_byte = resp[6]

        online = (resp[2] == 0x01) and (0 < battery <= 100)
        charging = (state_byte == 0x10)
        result["online"] = online
        result["battery"] = battery if online else None
        result["charging"] = charging
        if not online:
            result["status_text"] = "已休眠"
        elif charging:
            result["status_text"] = "充电中 ⚡"
        else:
            result["status_text"] = "无线使用中"
        return result
    except Exception:
        result["status_text"] = "读取失败"
        return result
    finally:
        try:
            h.close()
        except Exception:
            pass


def query_logitech_gpw():
    """通过 HID++ 2.0 查询罗技 GPW (LIGHTSPEED C547) 电量"""
    result = {
        "id": "mouse",
        "name": "罗技 G PRO Wireless",
        "Category": "鼠标 · LIGHTSPEED",
        "online": False,
        "battery": None,
        "charging": False,
        "status_text": "未连接接收器",
    }
    devs = [
        d for d in hid.enumerate(0x046D, 0xC547)
        if d.get("usage_page") == 0xFF00 and d.get("usage") == 0x02
    ]
    if not devs:
        return result

    h = hid.device()
    try:
        h.open_path(devs[0]["path"])
        h.set_nonblocking(1)
        while h.read(64):
            pass
        h.set_nonblocking(0)

        def hidpp_request(dev_idx, feat_idx, func_id, params=()):
            pkt = [0x11, dev_idx, feat_idx, ((func_id & 0x0F) << 4) | 0x0A]
            pkt.extend(params)
            pkt.extend([0x00] * (20 - len(pkt)))
            h.write(pkt)
            deadline = time.time() + 0.35
            while time.time() < deadline:
                r = h.read(64, timeout_ms=120)
                if not r:
                    break
                if r[0] in (0x10, 0x11) and r[1] == dev_idx:
                    if r[2] in (0xFF, 0x8F):
                        return None
                    if r[2] == feat_idx:
                        return r
            return None

        r_feat = hidpp_request(0x01, 0x00, 0x00, [0x10, 0x04])
        if r_feat and r_feat[4] != 0:
            feat_idx = r_feat[4]
            r_bat = hidpp_request(0x01, feat_idx, 0x01, [])
            if r_bat and 0 < r_bat[4] <= 100:
                bat = r_bat[4]
                chg_state = r_bat[6]
                charging = chg_state in (1, 2, 3)
                result["online"] = True
                result["battery"] = bat
                result["charging"] = charging
                result["status_text"] = "充电中" if charging else "无线使用中"
                return result

        r_feat = hidpp_request(0x01, 0x00, 0x00, [0x10, 0x00])
        if r_feat and r_feat[4] != 0:
            feat_idx = r_feat[4]
            r_bat = hidpp_request(0x01, feat_idx, 0x00, [])
            if r_bat and 0 < r_bat[4] <= 100:
                bat = r_bat[4]
                charging = r_bat[6] in (1, 2, 3, 4)
                result["online"] = True
                result["battery"] = bat
                result["charging"] = charging
                result["status_text"] = "充电中" if charging else "无线使用中"
                return result

        result["status_text"] = "已休眠 / 离线"
        return result
    except Exception:
        result["status_text"] = "读取失败"
        return result
    finally:
        try:
            h.close()
        except Exception:
            pass


def query_hyperx_cloud2():
    """查询 HyperX Cloud II Core Wireless (03F0:0995) 电量"""
    result = {
        "id": "headset",
        "name": "HyperX Cloud II Core",
        "Category": "耳机 · 2.4G",
        "online": False,
        "battery": None,
        "charging": False,
        "status_text": "未连接接收器",
    }
    devs = [
        d for d in hid.enumerate(0x03F0, 0x0995)
        if d.get("usage_page") == 0xFF13
    ]
    if not devs:
        return result

    h = hid.device()
    try:
        h.open_path(devs[0]["path"])
        h.set_nonblocking(1)
        while h.read(64):
            pass
        h.set_nonblocking(0)

        pkt_bat = [0x66, 0x89] + [0x00] * 60
        h.write(pkt_bat)
        resp_bat = h.read(64, timeout_ms=350)

        pkt_chg = [0x66, 0x8A] + [0x00] * 60
        h.write(pkt_chg)
        resp_chg = h.read(64, timeout_ms=250)

        if not resp_bat or len(resp_bat) < 6 or resp_bat[0] != 0x66:
            result["status_text"] = "未开机 / 离线"
            return result

        bat = resp_bat[4] if resp_bat[4] > 0 else resp_bat[2]
        if not (0 < bat <= 100):
            result["status_text"] = "未开机 / 离线"
            return result

        charging = bool(resp_chg and len(resp_chg) > 2 and resp_chg[2] == 0x01)
        result["online"] = True
        result["battery"] = bat
        result["charging"] = charging
        result["status_text"] = "充电中" if charging else "无线使用中"
        return result
    except Exception:
        result["status_text"] = "读取失败"
        return result
    finally:
        try:
            h.close()
        except Exception:
            pass


# ============================================================================
# 2. 开机自启注册表管理
# ============================================================================

def get_autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{os.path.abspath(sys.executable)}"'
    vbs_path = os.path.join(get_base_dir(), "启动电量托盘.vbs")
    return f'wscript.exe "{vbs_path}"'


def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart_enabled(enable: bool):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, get_autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except Exception:
        pass


# ============================================================================
# 3. Windows 11 原生字体与图标工具 (Segoe Fluent Icons)
# ============================================================================

def load_font(size, bold=False):
    candidates = [
        ("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        ("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def load_fluent_icon_font(size):
    for path in [FLUENT_ICON_FONT_PATH, "C:/Windows/Fonts/segmdl2.ttf"]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def get_battery_color(bat, online, charging, is_light_theme=True):
    if not online or bat is None:
        return (130, 130, 130) if is_light_theme else (138, 138, 138)
    if charging:
        return (0, 103, 192) if is_light_theme else (96, 205, 255)
    if bat <= 20:
        return (196, 43, 28) if is_light_theme else (255, 153, 164)
    if bat <= 40:
        return (157, 93, 0) if is_light_theme else (252, 225, 0)
    return (15, 123, 15) if is_light_theme else (108, 203, 95)


def create_tray_icon_image(devices=None):
    """
    固定使用 Windows 11 自带的 Segoe Fluent Icons (SegoeIcons.ttf) 标准电池图标 (0xEBAA)，
    不随充电状态变化，自动匹配 Windows 11 任务栏浅色/深色前景色，背景完全透明。
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    taskbar_light, _ = get_win11_light_prefs()
    ink_color = (26, 26, 26, 255) if taskbar_light else (255, 255, 255, 255)

    glyph = chr(0xEBAA)
    icon_font = load_fluent_icon_font(52)
    bbox = draw.textbbox((0, 0), glyph, font=icon_font)
    gw = bbox[2] - bbox[0]
    gh = bbox[3] - bbox[1]
    gx = (size - gw) // 2 - bbox[0]
    gy = (size - gh) // 2 - bbox[1]
    draw.text((gx, gy), glyph, font=icon_font, fill=ink_color)
    return img


# ============================================================================
# 4. Windows 11 悬浮卡片窗口 (支持浅色 & 深色双主题)
# ============================================================================

THEMES = {
    "light": {
        "win_bg_hex": "#F3F3F3",
        "win_bg": (243, 243, 243, 255),
        "win_border": (210, 210, 210, 255),
        "card_bg": (255, 255, 255),
        "card_border": (229, 229, 229),
        "icon_circle_bg": (245, 245, 245),
        "icon_circle_border": (224, 224, 224),
        "text_primary": (26, 26, 26),
        "text_secondary": (96, 96, 96),
        "text_muted": (130, 130, 130),
        "btn_bg": (255, 255, 255),
        "btn_hover": (236, 236, 236),
        "btn_border": (212, 212, 212),
        "bar_track": (230, 230, 230),
        "bar_offline": (180, 180, 180),
        "quit_hover_bg": (196, 43, 28),
        "quit_hover_fg": (255, 255, 255),
    },
    "dark": {
        "win_bg_hex": "#202020",
        "win_bg": (32, 32, 32, 255),
        "win_border": (60, 60, 60, 255),
        "card_bg": (45, 45, 45),
        "card_border": (62, 62, 62),
        "icon_circle_bg": (35, 35, 35),
        "icon_circle_border": (58, 58, 58),
        "text_primary": (245, 245, 245),
        "text_secondary": (165, 165, 165),
        "text_muted": (130, 130, 130),
        "btn_bg": (45, 45, 45),
        "btn_hover": (62, 62, 62),
        "btn_border": (70, 70, 70),
        "bar_track": (30, 30, 30),
        "bar_offline": (95, 95, 95),
        "quit_hover_bg": (196, 43, 28),
        "quit_hover_fg": (255, 255, 255),
    },
}

DEVICE_FLUENT_GLYPHS = {
    "keyboard": chr(0xE765),  # Win11 原生键盘图标
    "mouse": chr(0xE962),     # Win11 原生鼠标图标
    "headset": chr(0xE7F6),   # Win11 原生耳机图标
}


class Win11BatteryFlyout:
    WIDTH = 366
    HEIGHT = 368

    def __init__(self):
        cache = load_battery_cache()
        _, app_light = get_win11_light_prefs()
        default_theme = "light" if app_light else "dark"
        self.theme_mode = cache.get("theme", default_theme)
        if self.theme_mode not in ("light", "dark"):
            self.theme_mode = "light"

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=THEMES[self.theme_mode]["win_bg_hex"])

        self.canvas = tk.Canvas(
            self.root,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=THEMES[self.theme_mode]["win_bg_hex"],
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.root.bind("<FocusOut>", self._on_focus_out)
        self.root.bind("<Escape>", lambda e: self.hide())

        self.photo = None
        self.visible = False
        self.show_timestamp = 0.0
        self.refreshing = False
        self.last_updated = "--:--:--"
        self.update_status_text = APP_VERSION
        self.latest_release_url = None
        self.checking_update = False

        self.last_known_battery = {}
        if "keyboard" in cache and cache["keyboard"].get("battery") is not None:
            self.last_known_battery["keyboard"] = cache["keyboard"]["battery"]

        self.devices = [
            {"id": "keyboard", "name": "狼蛛 F87 Pro", "Category": "键盘 · 2.4G", "online": False, "battery": None, "charging": False, "status_text": "正在读取..."},
            {"id": "mouse", "name": "罗技 G PRO Wireless", "Category": "鼠标 · LIGHTSPEED", "online": False, "battery": None, "charging": False, "status_text": "正在读取..."},
            {"id": "headset", "name": "HyperX Cloud II Core", "Category": "耳机 · 2.4G", "online": False, "battery": None, "charging": False, "status_text": "正在读取..."},
        ]

        # 顶部与底部热区 (x0, y0, x1, y1)
        self.theme_btn_rect = (self.WIDTH - 90, 14, self.WIDTH - 58, 46)
        self.refresh_btn_rect = (self.WIDTH - 50, 14, self.WIDTH - 18, 46)
        self.version_btn_rect = (18, self.HEIGHT - 40, 260, self.HEIGHT - 14)
        self.quit_btn_rect = (self.WIDTH - 48, self.HEIGHT - 42, self.WIDTH - 18, self.HEIGHT - 14)
        self.hover_target = None

        self.tray_icon = None

    def toggle_theme(self):
        self.theme_mode = "dark" if self.theme_mode == "light" else "light"
        c = load_battery_cache()
        c["theme"] = self.theme_mode
        save_battery_cache(c)
        bg_hex = THEMES[self.theme_mode]["win_bg_hex"]
        self.root.configure(bg=bg_hex)
        self.canvas.configure(bg=bg_hex)
        self._apply_win11_corners()
        self.render_flyout()

    def _apply_win11_corners(self):
        """调用 Windows 11 DWM API 启用原生圆角与浅/深色边框阴影"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
            )
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            dark = ctypes.c_int(1 if self.theme_mode == "dark" else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark)
            )
        except Exception:
            pass

    def _position_near_tray(self):
        work_area = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0)
        x = work_area.right - self.WIDTH - 14
        y = work_area.bottom - self.HEIGHT - 12
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def render_flyout(self):
        """使用 2x 超采样渲染符合 Windows 11 Fluent Design 浅色/深色规范的面板"""
        s = 2
        W, H = self.WIDTH * s, self.HEIGHT * s
        t = THEMES[self.theme_mode]
        is_light = (self.theme_mode == "light")

        img = Image.new("RGBA", (W, H), t["win_bg"])
        draw = ImageDraw.Draw(img)

        f_title = load_font(16 * s, bold=True)
        f_sub = load_font(11 * s, bold=False)
        f_name = load_font(14 * s, bold=True)
        f_pct = load_font(18 * s, bold=True)
        f_icon_btn = load_fluent_icon_font(15 * s)
        f_icon_dev = load_fluent_icon_font(20 * s)

        def draw_centered_glyph(rect_2x, glyph, font, color):
            x0, y0, x1, y1 = rect_2x
            gb = draw.textbbox((0, 0), glyph, font=font)
            gw, gh = gb[2] - gb[0], gb[3] - gb[1]
            gx = x0 + (x1 - x0 - gw) // 2 - gb[0]
            gy = y0 + (y1 - y0 - gh) // 2 - gb[1]
            draw.text((gx, gy), glyph, font=font, fill=color)

        # 标题栏 + 更新时间紧跟标题后方
        title_str = "外设电量"
        draw.text((20 * s, 18 * s), title_str, font=f_title, fill=t["text_primary"])
        title_bbox = draw.textbbox((20 * s, 18 * s), title_str, font=f_title)
        draw.text(
            (title_bbox[2] + 10 * s, 23 * s),
            f"更新于 {self.last_updated}",
            font=f_sub,
            fill=t["text_muted"],
        )

        # 主题切换图标按钮 (太阳 0xE706 / 月亮 0xE708)
        tx0, ty0, tx1, ty1 = [v * s for v in self.theme_btn_rect]
        t_bg = t["btn_hover"] if self.hover_target == "theme" else t["btn_bg"]
        draw.rounded_rectangle([tx0, ty0, tx1, ty1], radius=8 * s, fill=t_bg, outline=t["btn_border"], width=1 * s)
        theme_glyph = chr(0xE706) if is_light else chr(0xE708)
        draw_centered_glyph((tx0, ty0, tx1, ty1), theme_glyph, f_icon_btn, t["text_primary"])

        # 立即刷新图标按钮 (刷新 0xE72C)
        rx0, ry0, rx1, ry1 = [v * s for v in self.refresh_btn_rect]
        r_bg = t["btn_hover"] if self.hover_target == "refresh" else t["btn_bg"]
        draw.rounded_rectangle([rx0, ry0, rx1, ry1], radius=8 * s, fill=r_bg, outline=t["btn_border"], width=1 * s)
        refresh_color = (0, 103, 192) if (self.refreshing and is_light) else ((96, 205, 255) if self.refreshing else t["text_primary"])
        draw_centered_glyph((rx0, ry0, rx1, ry1), chr(0xE72C), f_icon_btn, refresh_color)

        # 三个设备卡片
        card_top = 56 * s
        card_h = 80 * s
        card_gap = 10 * s

        for idx, dev in enumerate(self.devices):
            cy0 = card_top + idx * (card_h + card_gap)
            cy1 = cy0 + card_h
            cx0, cx1 = 18 * s, W - 18 * s

            draw.rounded_rectangle(
                [cx0, cy0, cx1, cy1],
                radius=10 * s,
                fill=t["card_bg"],
                outline=t["card_border"],
                width=1 * s,
            )

            online = dev["online"]
            bat = dev["battery"]
            charging = dev["charging"]
            cached_bat = self.last_known_battery.get(dev["id"])
            display_bat = bat if (online and bat is not None) else cached_bat
            accent = get_battery_color(display_bat, online, charging, is_light)

            # 左侧圆形图标底座 + Win11 原生 Segoe Fluent Icons 外设图标
            ic_x, ic_y = cx0 + 30 * s, cy0 + 34 * s
            draw.ellipse(
                [ic_x - 19 * s, ic_y - 19 * s, ic_x + 19 * s, ic_y + 19 * s],
                fill=t["icon_circle_bg"],
                outline=t["icon_circle_border"],
                width=1 * s,
            )
            dev_glyph = DEVICE_FLUENT_GLYPHS.get(dev["id"], chr(0xE765))
            gb = draw.textbbox((0, 0), dev_glyph, font=f_icon_dev)
            gw, gh = gb[2] - gb[0], gb[3] - gb[1]
            draw.text(
                (ic_x - gw // 2 - gb[0], ic_y - gh // 2 - gb[1]),
                dev_glyph,
                font=f_icon_dev,
                fill=accent,
            )

            # 设备名称与状态副标题
            tx = cx0 + 60 * s
            draw.text((tx, cy0 + 14 * s), dev["name"], font=f_name, fill=t["text_primary"])

            status_str = f"{dev['Category']}  ·  {dev['status_text']}"
            if not online and cached_bat is not None:
                status_str += f" (上次 {cached_bat}%)"
            draw.text((tx, cy0 + 37 * s), status_str, font=f_sub, fill=t["text_secondary"])

            # 右侧百分比大字
            if online and bat is not None:
                pct_str = f"{bat}%"
                pct_color = accent
            elif cached_bat is not None:
                pct_str = f"{cached_bat}%"
                pct_color = t["text_muted"]
            elif "等待推送" in dev["status_text"]:
                pct_str = "待推送"
                pct_color = (0, 103, 192) if is_light else (96, 205, 255)
            else:
                pct_str = "离线"
                pct_color = t["text_muted"]

            use_large_font = pct_str.endswith("%")
            pb = draw.textbbox((0, 0), pct_str, font=f_pct if use_large_font else f_name)
            pw = pb[2] - pb[0]
            draw.text(
                (cx1 - 16 * s - pw, cy0 + 16 * s),
                pct_str,
                font=f_pct if use_large_font else f_name,
                fill=pct_color,
            )

            # 底部进度条
            bar_x0 = tx
            bar_x1 = cx1 - 16 * s
            bar_y0 = cy1 - 14 * s
            bar_y1 = cy1 - 8 * s
            draw.rounded_rectangle([bar_x0, bar_y0, bar_x1, bar_y1], radius=3 * s, fill=t["bar_track"])

            if display_bat is not None and display_bat > 0:
                fill_w = max(6 * s, int((bar_x1 - bar_x0) * (display_bat / 100.0)))
                bar_color = accent if online else t["bar_offline"]
                draw.rounded_rectangle([bar_x0, bar_y0, bar_x0 + fill_w, bar_y1], radius=3 * s, fill=bar_color)

        # 底部栏：左下角版本号文本（点击检查更新） + 右下角退出图标按钮
        if self.latest_release_url:
            ver_color = (0, 103, 192) if is_light else (96, 205, 255)
        elif self.hover_target == "version":
            ver_color = t["text_primary"]
        else:
            ver_color = t["text_muted"]
        draw.text((20 * s, (self.HEIGHT - 34) * s), self.update_status_text, font=f_sub, fill=ver_color)

        # 退出图标按钮 (电源图标 0xE7E8)
        qx0, qy0, qx1, qy1 = [v * s for v in self.quit_btn_rect]
        is_quit_hover = (self.hover_target == "quit")
        q_bg = t["quit_hover_bg"] if is_quit_hover else t["btn_bg"]
        q_fg = t["quit_hover_fg"] if is_quit_hover else t["text_primary"]
        draw.rounded_rectangle([qx0, qy0, qx1, qy1], radius=7 * s, fill=q_bg, outline=t["btn_border"], width=1 * s)
        draw_centered_glyph((qx0, qy0, qx1, qy1), chr(0xE7E8), f_icon_btn, q_fg)

        final_img = img.resize((self.WIDTH, self.HEIGHT), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(final_img, master=self.root)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

    def _hit_test(self, x, y):
        for name, rect in [
            ("theme", self.theme_btn_rect),
            ("refresh", self.refresh_btn_rect),
            ("version", self.version_btn_rect),
            ("quit", self.quit_btn_rect),
        ]:
            if rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]:
                return name
        return None

    def _on_canvas_motion(self, event):
        target = self._hit_test(event.x, event.y)
        if target != self.hover_target:
            self.hover_target = target
            self.root.config(cursor="hand2" if target else "")
            self.render_flyout()

    def _on_canvas_click(self, event):
        target = self._hit_test(event.x, event.y)
        if target == "theme":
            self.toggle_theme()
        elif target == "refresh":
            self.trigger_refresh()
        elif target == "version":
            self.check_for_updates()
        elif target == "quit":
            self.quit_app()

    def check_for_updates(self):
        if self.latest_release_url:
            webbrowser.open(self.latest_release_url)
            return
        if self.checking_update:
            return
        self.checking_update = True
        self.update_status_text = f"{APP_VERSION} · 正在检查更新..."
        self.render_flyout()

        def _worker():
            try:
                url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
                req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                latest_tag = data.get("tag_name", "")
                html_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
                if latest_tag and latest_tag.lstrip("vV") != APP_VERSION.lstrip("vV"):
                    self.latest_release_url = html_url
                    self.update_status_text = f"{APP_VERSION} · 发现新版本 {latest_tag} (点击下载)"
                else:
                    self.update_status_text = f"{APP_VERSION} · 已是最新版本"
            except Exception:
                self.update_status_text = f"{APP_VERSION} · 检查更新失败 (点击重试)"
            finally:
                self.checking_update = False
                self.root.after(0, lambda: self.render_flyout() if self.visible else None)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_focus_out(self, _event=None):
        if time.time() - self.show_timestamp < 1.5:
            return
        self.hide()

    def show(self):
        self.show_timestamp = time.time()
        self._position_near_tray()
        self.render_flyout()
        self.root.deiconify()
        self._apply_win11_corners()
        self.root.lift()
        self.root.focus_force()
        self.visible = True

    def hide(self):
        if self.visible:
            self.root.withdraw()
            self.visible = False

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()
            self.trigger_refresh()

    def trigger_refresh(self):
        if self.refreshing:
            return
        self.refreshing = True
        if self.visible:
            self.render_flyout()
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        kb = query_aula_f87pro()
        ms = query_logitech_gpw()
        hs = query_hyperx_cloud2()
        new_devs = [kb, ms, hs]
        for d in new_devs:
            if d["online"] and d["battery"] is not None:
                self.last_known_battery[d["id"]] = d["battery"]

        self.devices = new_devs
        self.last_updated = time.strftime("%H:%M:%S")
        self.refreshing = False
        self.root.after(0, self._on_refresh_done)

    def _on_refresh_done(self):
        if self.visible:
            self.render_flyout()
        if self.tray_icon:
            self.tray_icon.icon = create_tray_icon_image(self.devices)
            lines = ["外设电量中心"]
            for d in self.devices:
                if d["online"] and d["battery"] is not None:
                    chg = " ⚡" if d["charging"] else ""
                    lines.append(f"{d['name']}: {d['battery']}%{chg}")
                else:
                    cached = self.last_known_battery.get(d["id"])
                    if cached is not None:
                        lines.append(f"{d['name']}: 上次 {cached}%")
                    elif "等待推送" in d["status_text"]:
                        lines.append(f"{d['name']}: 待推送")
                    else:
                        lines.append(f"{d['name']}: 离线")
            self.tray_icon.title = "\n".join(lines)[:127]

    def _periodic_poll(self):
        self.trigger_refresh()
        self.root.after(60000, self._periodic_poll)

    def quit_app(self):
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        self.root.quit()
        sys.exit(0)

    def _promote_tray_icon_in_win11(self):
        """将托盘图标在 Windows 11 中自动设为始终显示"""
        try:
            notify_path = r"Control Panel\NotifyIconSettings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, notify_path, 0, winreg.KEY_READ) as parent:
                idx = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(parent, idx)
                        idx += 1
                    except OSError:
                        break
                    sub_path = f"{notify_path}\\{sub_name}"
                    try:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as sk:
                            exe_val, _ = winreg.QueryValueEx(sk, "ExecutablePath")
                            exe_str = str(exe_val).lower()
                            if "python" in exe_str or "2.4gbattery" in exe_str:
                                winreg.SetValueEx(sk, "IsPromoted", 0, winreg.REG_DWORD, 1)
                    except Exception:
                        pass
        except Exception:
            pass

    def _start_exit_event_listener(self):
        """监听新实例发出的接管退出信号，收到后优雅移除托盘图标并退出"""
        def _wait_loop():
            kernel32 = ctypes.windll.kernel32
            h_evt = kernel32.CreateEventW(None, True, False, EXIT_EVENT_NAME)
            if not h_evt:
                return
            kernel32.WaitForSingleObject(h_evt, 0xFFFFFFFF)
            kernel32.CloseHandle(h_evt)
            self.root.after(0, self.quit_app)

        threading.Thread(target=_wait_loop, daemon=True).start()

    def run(self):
        menu = pystray.Menu(
            pystray.MenuItem("打开/收起电量面板", lambda icon, item: self.root.after(0, self.toggle), default=True),
            pystray.MenuItem("切换 浅色/深色 主题", lambda icon, item: self.root.after(0, self.toggle_theme)),
            pystray.MenuItem("立即刷新电量", lambda icon, item: self.root.after(0, self.trigger_refresh)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "开机自动启动",
                lambda icon, item: self.root.after(0, lambda: set_autostart_enabled(not is_autostart_enabled())),
                checked=lambda item: is_autostart_enabled(),
            ),
            pystray.MenuItem("退出", lambda icon, item: self.root.after(0, self.quit_app)),
        )
        self.tray_icon = pystray.Icon(
            APP_NAME,
            create_tray_icon_image(self.devices),
            "外设电量中心 - 正在读取...",
            menu,
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        self._start_exit_event_listener()
        self.root.after(100, self._periodic_poll)
        self.root.after(600, self.show)
        self.root.after(1200, self._promote_tray_icon_in_win11)
        self.root.mainloop()


if __name__ == "__main__":
    kill_previous_instances()
    if is_autostart_enabled():
        set_autostart_enabled(True)
    app = Win11BatteryFlyout()
    app.run()
