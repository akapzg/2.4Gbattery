import hid
import time

VID = 0x3554
PID = 0xFA09
USAGE_PAGE = 0xFF02
REPORT_ID = 0x13


def listen_f87pro_battery(timeout_sec=15):
    """
    监听狼蛛 F87 Pro 在 0xFF02 / Report ID 0x13 通道主动上报的 0x0A 状态帧：
    格式: 13 0A 01 00 | 04 05 <battery> <charge_state> | 00... | <checksum>
    """
    devs = [
        d for d in hid.enumerate(VID, PID)
        if d["usage_page"] == USAGE_PAGE
    ]
    if not devs:
        raise RuntimeError("未找到狼蛛 F87 Pro 2.4G 接收器 (3554:FA09 / 0xFF02)")

    h = hid.device()
    try:
        h.open_path(devs[0]["path"])
        h.set_nonblocking(0)
        print(f"正在监听 0xFF02 通道 (请在键盘上按任意键或 Fn+B 触发状态上报，等待 {timeout_sec} 秒)...")
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            resp = h.read(64, timeout_ms=500)
            if not resp or len(resp) < 20 or resp[0] != REPORT_ID:
                continue
            hex_frame = " ".join(f"{b:02X}" for b in resp[:20])
            print(f"收到报文: {hex_frame}")
            if resp[1] == 0x0A and resp[4] == 0x04 and resp[5] == 0x05:
                battery = resp[6]
                state_byte = resp[7]
                state_str = (
                    "充电中" if state_byte == 0x10
                    else "放电中" if state_byte == 0x01
                    else f"未知({state_byte:#x})"
                )
                return {
                    "online": resp[2] == 0x01,
                    "battery": battery,
                    "state": state_str,
                    "raw": hex_frame,
                }
        return None
    finally:
        h.close()


if __name__ == "__main__":
    info = listen_f87pro_battery(15)
    if info:
        print(f"当前真实电量: {info['battery']}% ({info['state']})")
    else:
        print("监听超时未收到 13 0A 04 05 电量帧（键盘仅在状态变化/唤醒/周期心跳时主动推送）。")
