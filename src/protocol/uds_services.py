"""UDS服务编解码 (ISO 14229-1)

定义所有核心UDS服务的SID、编解码方法。
"""

from enum import IntEnum
from src.models.uds_message import NRC_CODES


class ServiceID(IntEnum):
    """UDS服务标识符"""
    DIAGNOSTIC_SESSION_CONTROL = 0x10
    ECU_RESET = 0x11
    CLEAR_DIAGNOSTIC_INFORMATION = 0x14
    READ_DTC_INFORMATION = 0x19
    READ_DATA_BY_IDENTIFIER = 0x22
    READ_MEMORY_BY_ADDRESS = 0x23
    SECURITY_ACCESS = 0x27
    COMMUNICATION_CONTROL = 0x28
    READ_SCALING_DATA_BY_IDENTIFIER = 0x24
    INPUT_OUTPUT_CONTROL = 0x2F
    ROUTINE_CONTROL = 0x31
    REQUEST_DOWNLOAD = 0x34
    REQUEST_UPLOAD = 0x35
    TRANSFER_DATA = 0x36
    REQUEST_TRANSFER_EXIT = 0x37
    TESTER_PRESENT = 0x3E
    CONTROL_DTC_SETTING = 0x85
    WRITE_DATA_BY_IDENTIFIER = 0x2E


class NegativeResponseCode(IntEnum):
    """负响应码"""
    GENERAL_REJECT = 0x10
    SERVICE_NOT_SUPPORTED = 0x11
    SUB_FUNCTION_NOT_SUPPORTED = 0x12
    INCORRECT_MESSAGE_LENGTH = 0x13
    RESPONSE_TOO_LONG = 0x14
    BUSY_REPEAT_REQUEST = 0x21
    CONDITIONS_NOT_CORRECT = 0x22
    REQUEST_SEQUENCE_ERROR = 0x24
    NO_RESPONSE_FROM_SUBNET = 0x25
    FAILURE_PREVENTS_EXECUTION = 0x26
    REQUEST_OUT_OF_RANGE = 0x31
    SECURITY_ACCESS_DENIED = 0x33
    INVALID_KEY = 0x35
    EXCEEDED_NUMBER_OF_ATTEMPTS = 0x36
    REQUIRED_TIME_DELAY_NOT_EXPIRED = 0x37
    UPLOAD_DOWNLOAD_NOT_ACCEPTED = 0x70
    TRANSFER_DATA_SUSPENDED = 0x71
    GENERAL_PROGRAMMING_FAILURE = 0x72
    WRONG_BLOCK_SEQUENCE_COUNTER = 0x73
    RESPONSE_PENDING = 0x78
    SUB_FUNCTION_NOT_SUPPORTED_IN_SESSION = 0x7E
    SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F


# 服务名称映射
SERVICE_NAMES = {
    0x10: "DiagnosticSessionControl",
    0x11: "ECUReset",
    0x14: "ClearDiagnosticInformation",
    0x19: "ReadDTCInformation",
    0x22: "ReadDataByIdentifier",
    0x23: "ReadMemoryByAddress",
    0x24: "ReadScalingDataByIdentifier",
    0x27: "SecurityAccess",
    0x28: "CommunicationControl",
    0x2E: "WriteDataByIdentifier",
    0x2F: "InputOutputControl",
    0x31: "RoutineControl",
    0x34: "RequestDownload",
    0x35: "RequestUpload",
    0x36: "TransferData",
    0x37: "RequestTransferExit",
    0x3E: "TesterPresent",
    0x85: "ControlDTCSetting",
}


class UdsService:
    """UDS服务编解码工具类"""

    @staticmethod
    def get_service_name(sid: int) -> str:
        """获取服务名称"""
        return SERVICE_NAMES.get(sid, f"Unknown(0x{sid:02X})")

    @staticmethod
    def get_nrc_description(nrc: int) -> str:
        """获取NRC描述"""
        return NRC_CODES.get(nrc, f"Unknown(0x{nrc:02X})")

    @staticmethod
    def is_positive_response(response: bytes) -> bool:
        """检查是否正响应"""
        if not response:
            return False
        return 0x40 <= response[0] < 0x7F

    @staticmethod
    def is_negative_response(response: bytes) -> bool:
        """检查是否负响应"""
        if not response or len(response) < 3:
            return False
        return response[0] == 0x7F

    @staticmethod
    def is_pending_response(response: bytes) -> bool:
        """检查是否pending响应 (NRC=0x78)"""
        if not response or len(response) < 3:
            return False
        return response[0] == 0x7F and response[2] == 0x78

    # --- 服务编码方法 ---

    @staticmethod
    def encode_diagnostic_session(session_type: int) -> bytes:
        """编码诊断会话切换请求"""
        return bytes([0x10, session_type])

    @staticmethod
    def encode_ecu_reset(reset_type: int) -> bytes:
        """编码ECU复位请求"""
        return bytes([0x11, reset_type])

    @staticmethod
    def encode_security_access_request_seed(level: int) -> bytes:
        """编码安全访问请求种子

        level 为实际子功能值（奇数），如 1/9 → 27 01/27 09；
        发送密钥自动使用偶数（level+1）。
        """
        return bytes([0x27, level])

    @staticmethod
    def encode_security_access_send_key(level: int, key: bytes) -> bytes:
        """编码安全访问发送密钥（偶数子功能 = 请求种子等级+1）"""
        return bytes([0x27, level + 1]) + key

    @staticmethod
    def encode_read_did(did_id: int) -> bytes:
        """编码读取DID请求"""
        return bytes([0x22, (did_id >> 8) & 0xFF, did_id & 0xFF])

    @staticmethod
    def encode_write_did(did_id: int, data: bytes) -> bytes:
        """编码写入DID请求"""
        return bytes([0x2E, (did_id >> 8) & 0xFF, did_id & 0xFF]) + data

    @staticmethod
    def encode_read_memory(address: int, size: int, addr_len: int = 4, size_len: int = 4) -> bytes:
        """编码按地址读内存请求"""
        addr_bytes = address.to_bytes(addr_len, 'big')
        size_bytes = size.to_bytes(size_len, 'big')
        length_format = (addr_len << 4) | size_len
        return bytes([0x23, length_format]) + addr_bytes + size_bytes

    @staticmethod
    def encode_io_control(did_id: int, control_option: int, data: bytes = b"") -> bytes:
        """编码IO控制请求"""
        return bytes([0x2F, (did_id >> 8) & 0xFF, did_id & 0xFF, control_option]) + data

    @staticmethod
    def encode_routine_control(sub_func: int, routine_id: int, data: bytes = b"") -> bytes:
        """编码例程控制请求"""
        return bytes([0x31, sub_func, (routine_id >> 8) & 0xFF, routine_id & 0xFF]) + data

    @staticmethod
    def encode_request_download(data_format: int, addr: int, size: int,
                                 addr_len: int = 4, size_len: int = 4) -> bytes:
        """编码请求下载"""
        addr_bytes = addr.to_bytes(addr_len, 'big')
        size_bytes = size.to_bytes(size_len, 'big')
        length_format = (addr_len << 4) | size_len
        return bytes([0x34, data_format, length_format]) + addr_bytes + size_bytes

    @staticmethod
    def encode_transfer_data(block_seq: int, data: bytes) -> bytes:
        """编码传输数据"""
        return bytes([0x36, block_seq & 0xFF]) + data

    @staticmethod
    def encode_request_transfer_exit() -> bytes:
        """编码请求传输退出"""
        return bytes([0x37])

    @staticmethod
    def encode_tester_present(suppress_response: bool = False) -> bytes:
        """编码TesterPresent"""
        sub_func = 0x80 if suppress_response else 0x00
        return bytes([0x3E, sub_func])

    @staticmethod
    def encode_control_dtc_setting(on: bool = True) -> bytes:
        """编码DTC设置控制"""
        return bytes([0x85, 0x01 if on else 0x02])

    @staticmethod
    def encode_read_dtc_by_status(mask: int = 0xFF) -> bytes:
        """编码按状态读DTC"""
        return bytes([0x19, 0x02, mask])

    @staticmethod
    def encode_read_dtc_count() -> bytes:
        """编码读取DTC数量"""
        return bytes([0x19, 0x01])

    @staticmethod
    def encode_clear_dtc(group: int = 0xFFFFFF) -> bytes:
        """编码清除DTC"""
        return bytes([0x14, (group >> 16) & 0xFF, (group >> 8) & 0xFF, group & 0xFF])

    @staticmethod
    def encode_communication_control(control_type: int, comm_type: int = 0x01) -> bytes:
        """编码通信控制"""
        return bytes([0x28, control_type, comm_type])

    # --- 响应解码方法 ---

    @staticmethod
    def decode_diagnostic_session(response: bytes) -> dict:
        """解码诊断会话正响应"""
        if len(response) < 4:
            return {}
        return {
            "session_type": response[1],
            "p2_high": (response[2] << 8 | response[3]) * 0.001,  # ms -> s
            "p2_star_high": (response[4] << 8 | response[5]) * 0.01 if len(response) > 5 else 0,
        }

    @staticmethod
    def decode_read_did(response: bytes) -> dict:
        """解码读取DID正响应"""
        if len(response) < 3:
            return {}
        did_id = (response[1] << 8) | response[2]
        return {
            "did_id": did_id,
            "data": response[3:],
        }

    @staticmethod
    def decode_security_seed(response: bytes) -> dict:
        """解码安全访问种子响应"""
        if len(response) < 2:
            return {}
        return {
            "level": response[1],
            "seed": response[2:],
        }

    @staticmethod
    def decode_read_dtc(response: bytes) -> dict:
        """解码读取DTC正响应"""
        if len(response) < 3:
            return {}
        sub_func = response[1]
        dtc_records = []
        if sub_func == 0x02 or sub_func == 0x0A:
            data = response[3:]
            for i in range(0, len(data), 4):
                if i + 3 < len(data):
                    dtc_id = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
                    status = data[i + 3]
                    dtc_records.append({"dtc_id": dtc_id, "status": status})
        elif sub_func == 0x01:
            count = response[3] if len(response) > 3 else 0
            return {"count": count}
        return {"sub_function": sub_func, "records": dtc_records}

    @staticmethod
    def decode_routine_control(response: bytes) -> dict:
        """解码例程控制正响应"""
        if len(response) < 4:
            return {}
        routine_id = (response[2] << 8) | response[3]
        return {
            "sub_function": response[1],
            "routine_id": routine_id,
            "data": response[4:] if len(response) > 4 else b"",
        }

    @staticmethod
    def decode_negative_response(response: bytes) -> dict:
        """解码负响应"""
        if len(response) < 3 or response[0] != 0x7F:
            return {}
        return {
            "rejected_sid": response[1],
            "nrc": response[2],
            "nrc_description": NRC_CODES.get(response[2], "Unknown"),
        }
