"""总线监听诊断 - 独立于DiagTools主程序的最小验证工具

用法: python tools/bus_listen.py [通道名] [秒数]
默认: PCAN_USBBUS1 10秒

适用: OBD诊断专用CAN（无周期应用报文，总线平时静默）

做什么:
  1. 纯监听统计总线帧（诊断专用CAN无背景流量属正常）
  2. 向 0x714（DASH定制诊断地址）重复发送 3E 00 并等待响应

判读:
  - 探测有响应 → 物理层与ECU正常，问题在DiagTools侧配置
  - 无响应 → ECU休眠（需断电重上电/开点火）或物理层问题
  - 本工具只用python-can，与DiagTools主程序零关联
"""
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

channel = sys.argv[1] if len(sys.argv) > 1 else "PCAN_USBBUS1"
duration = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

import can


def listen(bus, seconds: float, tag: str = "") -> list:
    """监听指定时长，返回收到的帧列表"""
    frames = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        msg = bus.recv(timeout=0.1)
        if msg is None:
            continue
        frames.append(msg)
        data = " ".join(f"{b:02X}" for b in msg.data)
        print(f"  RX 0x{msg.arbitration_id:03X}  {data}  {tag}")
    return frames


def probe(bus, can_id: int, payload: list[int], name: str,
          repeat: int = 3, wait: float = 1.5) -> list:
    """发送探测帧（可重复）并等待响应"""
    print(f"\n[{name}] 发送 0x{can_id:03X} -> "
          + " ".join(f"{b:02X}" for b in payload)
          + (f" x{repeat}" if repeat > 1 else ""))
    for i in range(repeat):
        bus.send(can.Message(arbitration_id=can_id,
                             data=payload + [0] * (8 - len(payload)),
                             is_extended_id=False))
        time.sleep(0.05)
    return listen(bus, wait, f"({name})")


print(f"打开 {channel} @ 500000bps ...")
bus = can.Bus(channel=channel, interface="pcan", bitrate=500000)

# 第一步: 纯监听（诊断专用CAN无帧属正常，仅确认无背景流量）
print(f"--- 纯监听 {duration}s（OBD专用总线静默属正常）---")
idle_frames = listen(bus, duration)
print(f"--- 监听结束: 收到 {len(idle_frames)} 帧 ---")

# 第二步: 物理寻址探测 DASH 定制诊断地址（重复发送冲击休眠ECU）
responses = probe(bus, 0x714, [0x02, 0x3E, 0x00], "DASH物理寻址 3E 00")

if responses:
    ids = sorted({f"0x{m.arbitration_id:03X}" for m in responses})
    print(f"\n结论: ECU有响应！响应ID: {', '.join(ids)}")
    print("  物理层正常。若DiagTools仍读不到，检查:")
    print("  - 响应ID是否与ECU定义的rx_id一致（0x794?）")
else:
    print("\n结论: 0x714 探测无响应（OBD专用总线静默属正常，")
    print("  但物理寻址 3E 00 连发也无回复说明ECU未醒或不在线）")
    print("  1. ECU深度休眠 → 断电重新上电（熄火拔钥匙或断台架电源）")
    print("  2. 检查点火信号（KL15/RUN档，很多OBD节点点火关闭即休眠）")
    print("  3. 波特率不是500k → 修改脚本内bitrate为250000/1000000再试")
    print("  4. 物理层: CANH/CANL接线、终端电阻（CANH-CANL约60Ω）")
    print("  建议用PEAK官方PCAN-View交叉验证")

bus.shutdown()

