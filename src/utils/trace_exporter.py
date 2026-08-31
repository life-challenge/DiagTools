"""Trace标准格式导出工具

将会话级报文记录导出为业界标准格式，便于发给他人用CANoe/CANalyzer/
Wireshark等工具分析：
  - ASC  (Vector ASCII日志)  —— CAN报文，纯文本，任何版本CANoe可读
  - BLF  (Vector二进制日志)  —— CAN报文，体积小，CANoe原生格式
  - pcap (libpcap抓包文件)   —— DoIP/以太网报文，Wireshark原生格式
    DoIP报文按 UDP(13400) + DoIP诊断消息 重建，源/目的逻辑地址由
    导出时的DoIP上下文提供。

entries 为 LogEntry 风格对象（timestamp/direction/can_id/data）。
ASC/BLF 依赖 python-can（项目已有依赖）；pcap 为纯手工构造。
"""

import socket
import struct
from typing import Optional

# DoIP帧常量（与 doip_layer 保持一致）
_DOIP_VERSION = 0x02
_DOIP_DIAG_MSG = 0x8001        # 诊断消息（ECU->Tester响应承载于0x8002正确认）
_DOIP_DIAG_POS_ACK = 0x8002

# pcap全局头: magic / version 2.4 / timezone / sigfigs / snaplen / LINKTYPE_ETHERNET
_PCAP_GLOBAL_HEADER = struct.pack(
    "<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)

# 伪MAC（本地管理地址，避免与真实网卡冲突）
_TESTER_MAC = bytes.fromhex("020001020304")
_ECU_MAC = bytes.fromhex("020001020305")

_DOIP_UDP_PORT = 13400


def _entries_to_can_messages(entries):
    """LogEntry列表 -> python-can Message列表"""
    import can
    msgs = []
    for e in entries:
        can_id = int(getattr(e, "can_id", 0)) & 0x1FFFFFFF
        msgs.append(can.Message(
            timestamp=float(e.timestamp),
            arbitration_id=can_id,
            data=bytes(e.data),
            is_extended_id=can_id > 0x7FF,
            is_rx=(e.direction != "TX"),
        ))
    return msgs


def write_asc(entries, path: str):
    """导出为Vector ASC格式（CAN）"""
    from can.io import ASCWriter
    with ASCWriter(path) as writer:
        for msg in _entries_to_can_messages(entries):
            writer.on_message_received(msg)


def write_blf(entries, path: str):
    """导出为Vector BLF格式（CAN，二进制）"""
    from can.io import BLFWriter
    with BLFWriter(path) as writer:
        for msg in _entries_to_can_messages(entries):
            writer.on_message_received(msg)


def write_pcap(entries, path: str,
               tester_addr: int = 0x0E80, ecu_addr: int = 0x1000,
               src_ip: str = "127.0.0.1", dst_ip: str = "127.0.0.1"):
    """导出为pcap格式（DoIP/以太网）

    每条记录重建完整报文链: Ethernet + IPv4 + UDP(13400) + DoIP诊断消息。
    TX方向: Tester->ECU（DoIP 0x8001）；RX方向: ECU->Tester（DoIP 0x8002）。

    Args:
        entries: LogEntry列表（DoIP会话的UDS收发记录）
        path: 输出文件路径
        tester_addr: Tester逻辑地址
        ecu_addr: ECU逻辑地址
        src_ip/dst_ip: 重建报文的IP地址（默认本机回环）
    """
    src_ip_bytes = socket.inet_aton(src_ip)
    dst_ip_bytes = socket.inet_aton(dst_ip)

    with open(path, "wb") as f:
        f.write(_PCAP_GLOBAL_HEADER)
        for e in entries:
            packet = _build_doip_ethernet_packet(
                e, tester_addr, ecu_addr, src_ip_bytes, dst_ip_bytes)
            ts = float(e.timestamp)
            sec = int(ts)
            usec = int(round((ts - sec) * 1_000_000))
            f.write(struct.pack("<IIII", sec, usec, len(packet), len(packet)))
            f.write(packet)


def _build_doip_ethernet_packet(entry, tester_addr: int, ecu_addr: int,
                                src_ip: bytes, dst_ip: bytes) -> bytes:
    """构造单条记录的完整以太网帧"""
    is_tx = entry.direction == "TX"
    # 源/目的逻辑地址: TX为Tester->ECU(0x8001)，RX为ECU->Tester(0x8002)
    src_logical = tester_addr if is_tx else ecu_addr
    dst_logical = ecu_addr if is_tx else tester_addr
    payload_type = _DOIP_DIAG_MSG if is_tx else _DOIP_DIAG_POS_ACK

    # DoIP帧: 头(8) + 源地址(2) + 目标地址(2) + UDS数据
    doip_payload = struct.pack("!HH", src_logical, dst_logical) + bytes(entry.data)
    doip = struct.pack("!BBHI", _DOIP_VERSION, 0xFF ^ _DOIP_VERSION,
                       payload_type, len(doip_payload)) + doip_payload

    # UDP头: 源/目的端口均为DoIP标准13400，校验和0（IPv4允许）
    udp = struct.pack("!HHHH", _DOIP_UDP_PORT, _DOIP_UDP_PORT,
                      8 + len(doip), 0) + doip

    # IPv4头（20字节，无选项）
    total_length = 20 + len(udp)
    header = struct.pack("!BBHHHBBH", 0x45, 0x00, total_length,
                         0x0001, 0x4000, 64, 17, 0) + src_ip + dst_ip
    checksum = _ipv4_checksum(header)
    header = header[:10] + struct.pack("!H", checksum) + header[12:]

    # 以太网帧: 目的MAC + 源MAC + EtherType IPv4
    eth = (_ECU_MAC + _TESTER_MAC if is_tx else _TESTER_MAC + _ECU_MAC) \
        + b"\x08\x00"

    return eth + header + udp


def _ipv4_checksum(header: bytes) -> int:
    """IPv4头校验和（16位反码和）"""
    if len(header) % 2:
        header += b"\x00"
    total = sum(struct.unpack(f"!{len(header) // 2}H", header))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def export_entries(entries, path: str,
                   doip_context: Optional[tuple] = None) -> str:
    """按文件扩展名自动选择格式导出

    Args:
        entries: LogEntry列表
        path: 输出路径（.asc/.blf/.pcap/.csv）
        doip_context: (tester_addr, ecu_addr)，pcap导出时的逻辑地址

    Returns:
        实际使用的格式描述（用于状态提示）；不支持的扩展名返回""
    """
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    if ext == "asc":
        write_asc(entries, path)
        return "ASC (Vector ASCII)"
    if ext == "blf":
        write_blf(entries, path)
        return "BLF (Vector Binary)"
    if ext == "pcap":
        tester, ecu = doip_context or (0x0E80, 0x1000)
        write_pcap(entries, path, tester, ecu)
        return "pcap (DoIP over Ethernet)"
    if ext == "csv":
        _write_csv(entries, path)
        return "CSV"
    if ext == "txt":
        _write_txt(entries, path)
        return "文本"
    return ""


def _write_csv(entries, path: str):
    """CSV导出（保留原有格式）"""
    with open(path, "w", encoding="utf-8") as f:
        f.write("时间,方向,CAN ID,数据,描述\n")
        for e in entries:
            data_hex = " ".join(f"{b:02X}" for b in e.data)
            desc = (getattr(e, "description", "") or "").replace(",", ";")
            f.write(f"{e.time_str},{e.direction},"
                    f"0x{e.can_id:03X},{data_hex},{desc}\n")


def _write_txt(entries, path: str):
    """纯文本导出"""
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            data_hex = " ".join(f"{b:02X}" for b in e.data)
            f.write(f"{e.time_str} [{e.direction}] "
                    f"0x{e.can_id:03X} {data_hex} {e.description}\n")
