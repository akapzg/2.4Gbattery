# AGENTS.md — 开发者与 AI Agent 维护指南

本文档面向后续维护、扩展本仓库的 AI Coding Agent（如 Antigravity、Claude Code、Cursor 等）。在对硬件 HID 协议或 UI 进行修改前，**必须先阅读以下安全红线与架构约定**。

---

## ⚠️ 1. 硬件安全红线（严禁触碰写入寄存器）

1. **严禁在 `3554:FA09` (`Usage Page 0xFF02`, `Report ID 0x13`) 上遍历或发送 `0x01`–`0x0F` 开头的指令！**
   - Compx 芯片协议中，`0x4X` 为只读查询指令（如 `0x4A` 查状态、`0x44` 读灯光、`0x41` 读键位），而 **`0x01`–`0x0F` 是写入指令**（例如发送 `13 07 01 00...` 会直接将键盘板载灯光配置覆盖重置为默认模式！）。
   - 如果用户灯光因误发写入指令被重置，可引导用户启动本地官方驱动 `C:\Program Files (x86)\AULA\F87 PRO\OemDrv.exe` 从 `C:\Users\<User>\AppData\Local\BYCOMBO4\AULA F87 PRO\profile.dct` 恢复。
2. **所有外设查询必须保持 100% 只读 (Read-Only)**：
   - 狼蛛 F87 Pro：仅允许发送 `[0x13, 0x4A, 0x00, ..., 0x5D]` 或被动调用 `h.read()` 接收 `0x0A` 状态帧。
   - 罗技 GPW：仅允许调用 HID++ 2.0 Feature `0x0000` (Root)、`0x1004` (Unified Battery) 和 `0x1000` (Battery Status) 的只读查询函数。
   - HyperX Cloud II Core：仅允许发送 `[0x66, 0x89, ...]` 和 `[0x66, 0x8A, ...]`。

---

## 🧩 2. 协议细节与逆向备忘

### 2.1 狼蛛 F87 Pro (`3554:FA09`, `Fw=24`)
- 通过逆向官方驱动 `OemDrv.exe` (`CDevComboFilm` 类，偏移 `0x88cd0` 与 `0x89890`) 确认：
  - 报文固定 20 字节，`resp[0] == 0x13`，`resp[19] == sum(resp[:19]) & 0xFF`。
  - `resp[4] & 0x0F` 为当前包的有效载荷字节数（Payload Length）：
    - 在被动推送帧 `13 0A 01 00 04 05 <bat> <chg> ...` 中，`resp[4] == 0x04`（4 字节载荷），`resp[5] == 0x05` 代表电量事件，`resp[6]` 为电量百分比，`resp[7]` 为充电状态（`0x10` 充电 / `0x01` 放电）。
    - 在主动响应帧 `13 4A 01 00 02 <bat> <chg> ...` 中，`resp[4] == 0x02`（2 字节载荷），`resp[5]` 为电量百分比，`resp[6]` 为充电状态（`0x10` 充电 / `0x01` 放电）。
  - 注意：`query_aula_f87pro()` 同时兼容上述两种帧偏移，请勿删除任一分支。

### 2.2 罗技 GPW (`046D:C547`)
- 必须打开 `usage_page == 0xFF00` 且 `usage == 0x02` 的长报告接口（Report ID `0x11`，20 字节）。
- 当鼠标休眠或关闭电源时，接收器会返回 `r[2] == 0xFF` 或 `0x8F` 的错误帧，此时应标记 `online = False` 并保留 `self.last_known_battery["mouse"]` 缓存值。

### 2.3 HyperX Cloud II Core Wireless (`03F0:0995`)
- 打开 `usage_page == 0xFF13` 接口，发送 62 字节报告 `[0x66, 0x89] + [0]*60`。
- 当耳机未开机时，接收器仍会回复 `[0x66, 0x89, 0x00, 0x00, 0x00, ...]`；只有当 `resp[4] > 0`（或 `resp[2] > 0`）时才代表耳机在线且返回了有效电量。

---

## 🎨 3. UI 渲染与 Windows 11 集成约定

1. **禁止绘制内层圆角外框**：
   - 窗口已通过 `DwmSetWindowAttribute(hwnd, 33, DWMWCP_ROUND=2)` 启用 Windows 11 原生 8px 硬件圆角与系统描边。
   - **切勿**在 `render_flyout()` 的 `img` 最外圈再画 `draw.rounded_rectangle` 外框，否则会与 DWM 原生圆角产生“双圆角线”重影。
2. **图标字体 (`SegoeIcons.ttf`)**：
   - 统一使用 `C:/Windows/Fonts/SegoeIcons.ttf` (`Segoe Fluent Icons`) 绘制矢量图标：
     - 托盘电池图标：固定使用 `\uEBAA`（标准满格电池字形），**不要**随充电状态切换为带闪电的码位。
     - 键盘：`\uE765`，鼠标：`\uE962`，耳机：`\uE7F6`。
     - 浅色太阳：`\uE706`，深色月亮：`\uE708`，刷新：`\uE72C`，退出电源：`\uE7E8`。
3. **进程管理与启动**：
   - 在 Windows Store 版 Python 环境中，后台进程名为 `pythonw3.13.exe`（而非 `pythonw.exe`）。重启或清理旧实例时请使用：
     ```powershell
     Stop-Process -Name "pythonw*","python3*" -Force -ErrorAction SilentlyContinue
     ```
   - AI 沙箱终端（`Shell_TrayWnd == 0x0`）无法直接向用户桌面 `explorer.exe` 注册托盘图标，修改代码后应提示用户通过 `Win + R` 或双击 `启动电量托盘.vbs` 在桌面会话启动。

---

## ➕ 4. 如何新增其他 2.4G 外设

1. 在 `tray_app.py` 第一部分新增 `query_<device_name>()` 函数，返回标准字典格式：
   ```python
   {
       "id": "unique_id",
       "name": "显示名称",
       "Category": "类别 · 连接方式",
       "online": bool,
       "battery": int | None,  # 0~100
       "charging": bool,
       "status_text": "无线使用中 / 充电中 ⚡ / 未开机",
   }
   ```
2. 在 `DEVICE_FLUENT_GLYPHS` 中为其 `id` 绑定对应的 `SegoeIcons.ttf` Unicode 码位。
3. 在 `Win11BatteryFlyout._refresh_worker()` 中将其加入 `new_devs` 列表，并按卡片数量（每张卡片高度 `80px + 10px` 间距）相应调整 `Win11BatteryFlyout.HEIGHT`。
