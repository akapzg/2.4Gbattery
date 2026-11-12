# 2.4Gbattery — Windows 11 原生风格 2.4G 外设电量托盘中心

无需安装任何体量庞大的官方驱动（如罗技 G HUB、狼蛛驱动、HyperX NGENUITY），直接通过 Windows 原生 **USB HID 自定义通信通道** 读取 2.4G 无线外设接收器报文，在 Windows 11 右下角系统托盘提供原生 Fluent Design 风格的外设电量面板。

---

## ✨ 功能特性

- **零官方驱动依赖**：纯 Python + `hidapi` 底层通信，不驻留任何厂商后台服务。
- **支持三类常见 2.4G 无线外设协议**：
  1. **狼蛛 AULA F87 Pro 无线键盘** (`3554:FA09` · Compx 2.4G 方案)
  2. **罗技 G PRO Wireless (GPW) 无线鼠标** (`046D:C547` · LIGHTSPEED HID++ 2.0 方案)
  3. **HyperX Cloud II Core Wireless 无线耳机** (`03F0:0995` · 惠普/HyperX 2.4G 方案)
- **原生 Windows 11 视觉体验**：
  - 调用 Windows 11 DWM API (`DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND`) 实现硬件级圆角与阴影。
  - 直接调用系统内置 `Segoe Fluent Icons` (`SegoeIcons.ttf`) 渲染任务栏原生电池图标、外设图标及顶部/底部功能图标按钮。
  - 2x 超采样抗锯齿绘制，支持 **浅色 (Light)** / **深色 (Dark)** 双主题一键切换并自动记忆。
- **轻量常驻与开机自启**：
  - 内置 Windows 11 托盘图标自动提权（写入 `NotifyIconSettings\IsPromoted = 1`，避免被折叠进 `^` 隐藏菜单）。
  - 面板内一键开关 **开机自启**，每 60 秒后台静默刷新，设备休眠时自动保留上次已知电量。

---

## 🚀 快速开始

### 1. 安装依赖

确保已安装 Python 3.10+，执行：

```powershell
pip install -r requirements.txt
```

### 2. 启动系统托盘

- **无黑框静默启动（推荐）**：直接双击仓库根目录下的 `启动电量托盘.vbs`，或按 `Win + R` 运行：
  ```text
  wscript.exe "完整路径\启动电量托盘.vbs"
  ```
- **命令行调试启动**：
  ```powershell
  python tray_app.py
  ```
- **单独命令行监听/查询键盘报文**：
  ```powershell
  python battery.py
  ```

---

## 🔬 硬件通信协议说明

### 1. 狼蛛 AULA F87 Pro (`VID: 0x3554`, `PID: 0xFA09`)

- **通信通道**：`Usage Page 0xFF02` / `Usage 0x0002` / `Report ID 0x13`（定长 20 字节，末字节为前 19 字节累加和 `& 0xFF`）。
- **主动查询帧 (`0x4A`)**：
  - 发送：`13 4A 00 00 ... 00 5D`
  - 响应：`13 4A <online> <seq> 02 <battery> <charge_state> 00 ... <checksum>`
- **被动状态推送帧 (`0x0A`)**：
  - 响应：`13 0A <online> <seq> 04 05 <battery> <charge_state> 00 ... <checksum>`
- **字段释义**：
  - `<online>`：`0x01` 在线。
  - `<battery>`：`0x00`–`0x64`（0%–100%）。
  - `<charge_state>`：`0x01` 无线放电中，`0x10` USB 充电中。
- **固件说明 (`Fw=24`)**：狼蛛采用锂电池端电压阶梯查表法（配合键盘 `Fn + B` 10 格灯条），在 `3.95V–4.20V` 高电压平台期或插线充电时会持续报告 `100%` (`0x64`)，待电压跨过平台期阈值后按阶梯下降。

### 2. 罗技 G PRO Wireless (`VID: 0x046D`, `PID: 0xC547`)

- **通信通道**：`Usage Page 0xFF00` / `Usage 0x0002` / `Report ID 0x11`（20 字节 HID++ 2.0 长报告）。
- **查询流程**：
  1. 通过 Root Feature (`0x0000`) 查询 `0x1004` (*Unified Battery*) 或 `0x1000` (*Battery Unified Level Status*) 对应的动态 Feature Index。
  2. 调用该 Feature Index 的 `get_status` 函数读取实时电量百分比（`1%` 步进精度）与充电状态。

### 3. HyperX Cloud II Core Wireless (`VID: 0x03F0`, `PID: 0x0995`)

- **通信通道**：`Usage Page 0xFF13` / `Usage 0x0001` / `Report ID 0x66`（62 字节报告）。
- **查询指令**：
  - 电量查询：`66 89 00 ... 00` (`HYPERX_CMD_GET_BATTERY`)，耳机开机时 `resp[4]`（或 `resp[2]`）返回 `1–100` 电量百分比；关机时全返回 `0x00`。
  - 充电查询：`66 8A 00 ... 00` (`HYPERX_CMD_GET_CHARGING`)，`resp[2] == 0x01` 表示正在充电。

---

## 📁 目录结构

| 文件 | 说明 |
| :--- | :--- |
| `tray_app.py` | Windows 11 托盘与悬浮面板主程序 |
| `battery.py` | 狼蛛 F87 Pro 独立命令行监听/调试脚本 |
| `启动电量托盘.vbs` | Windows 无控制台黑框静默启动脚本 |
| `requirements.txt` | Python 依赖列表 (`hidapi`, `Pillow`, `pystray`) |
| `AGENTS.md` | 面向 AI 编码 Agent 的架构、协议禁忌与扩展指南 |
