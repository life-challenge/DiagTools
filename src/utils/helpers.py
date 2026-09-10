"""通用辅助函数"""



def bytes_to_hex(data: bytes, separator: str = " ") -> str:
    """字节数据转HEX字符串"""
    return separator.join(f"{b:02X}" for b in data)


def hex_to_bytes(hex_str: str) -> bytes:
    """HEX字符串转字节数据
    
    支持格式: "10 03", "1003", "0x10 0x03", "10,03"
    """
    hex_str = hex_str.strip()
    # 移除常见分隔符和前缀
    hex_str = hex_str.replace(",", " ").replace("0x", "").replace("0X", "")
    parts = hex_str.split()
    result = bytearray()
    for part in parts:
        # 处理连续HEX字符串 (如 "1003" -> [0x10, 0x03])
        if len(part) > 2:
            for i in range(0, len(part), 2):
                byte_str = part[i:i + 2]
                if len(byte_str) == 2:
                    result.append(int(byte_str, 16))
        elif len(part) == 2:
            result.append(int(part, 16))
        elif len(part) == 1:
            result.append(int(part, 16))
    return bytes(result)


def is_valid_hex(hex_str: str) -> bool:
    """检查字符串是否为有效HEX格式"""
    try:
        hex_to_bytes(hex_str)
        return True
    except (ValueError, TypeError):
        return False


def format_can_id(can_id: int, extended: bool = False) -> str:
    """格式化CAN ID显示"""
    if extended:
        return f"0x{can_id:08X}"
    return f"0x{can_id:03X}"


def parse_can_id(id_str: str) -> int:
    """解析CAN ID字符串"""
    id_str = id_str.strip()
    if id_str.startswith("0x") or id_str.startswith("0X"):
        return int(id_str, 16)
    return int(id_str, 16)


def format_timestamp(ts: float, mode: str = "absolute", ref_ts: float = 0.0) -> str:
    """格式化时间戳
    
    Args:
        ts: 时间戳（秒）
        mode: 模式 - "absolute"(绝对), "relative"(相对首条), "delta"(距上条)
        ref_ts: 参考时间戳
    """
    from datetime import datetime

    if mode == "absolute":
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%H:%M:%S.%f")[:-4]  # 精确到0.1ms
    elif mode == "relative":
        delta = ts - ref_ts
        return f"+{delta:.4f}"
    elif mode == "delta":
        delta = ts - ref_ts
        return f"Δ{delta:.4f}"
    return f"{ts:.4f}"


def calculate_crc32(data: bytes) -> int:
    """计算CRC32校验和"""
    import binascii
    return binascii.crc32(data) & 0xFFFFFFFF


def chunk_bytes(data: bytes, chunk_size: int) -> list[bytes]:
    """将字节数据分块"""
    return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]


def int_to_bytes(value: int, length: int, byte_order: str = "big") -> bytes:
    """整数转字节"""
    return value.to_bytes(length, byteorder=byte_order)


def bytes_to_int(data: bytes, byte_order: str = "big") -> int:
    """字节转整数"""
    return int.from_bytes(data, byteorder=byte_order)
