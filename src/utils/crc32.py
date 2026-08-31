"""CRC32 校验模块（刷写完整性校验用）

参数（等价于标准 CRC-32/ISO-HDLC，即 zlib/binascii.crc32）:
    多项式   0x04C11DB7
    初始值   0xFFFFFFFF
    输入反转 refin  = True
    输出反转 refout = True
    结果异或 xorout = 0xFFFFFFFF

标准校验向量: b"123456789" (31 32 ... 39) -> 0xCBF43926
"""

import binascii


def _reflect(value: int, width: int) -> int:
    """按位反转（低width位）"""
    result = 0
    for _ in range(width):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def crc32_flash(data: bytes) -> int:
    """计算刷写数据CRC32（逐位实现，参数按规格显式展开）

    Args:
        data: 刷写的应用数据（APP内容，不含文件头）

    Returns:
        32位校验和，如 0xCBF43926
    """
    poly = 0x04C11DB7
    crc = 0xFFFFFFFF
    for byte in data:
        # 输入反转: 逐字节低位对齐到寄存器高位
        crc ^= _reflect(byte, 8) << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ poly) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    # 输出反转后与 0xFFFFFFFF 异或
    return _reflect(crc, 32) ^ 0xFFFFFFFF


def crc32(data: bytes) -> int:
    """CRC32（标准库实现，与 crc32_flash 结果完全一致，大数据量更快）"""
    return binascii.crc32(data) & 0xFFFFFFFF


def crc32_bytes(data: bytes) -> bytes:
    """CRC32 结果，大端4字节（用于例程选项记录）"""
    return crc32(data).to_bytes(4, "big")


# 模块加载自检: 标准校验向量
assert crc32_flash(b"123456789") == 0xCBF43926, "CRC32自检失败"
assert crc32(b"123456789") == 0xCBF43926
