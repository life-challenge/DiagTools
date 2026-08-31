"""UDS报文数据模型"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class UdsMessageType(Enum):
    """UDS报文类型"""
    REQUEST = "request"          # 请求
    POSITIVE_RESPONSE = "positive"  # 正响应
    NEGATIVE_RESPONSE = "negative"  # 负响应


# 负响应码 (NRC) 定义
NRC_CODES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnetComponent",
    0x26: "failurePreventsExecutionOfRequestedAction",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x38: "secureDataTransmissionRequired",
    0x39: "secureDataTransmissionNotAllowed",
    0x3A: "secureDataVerificationFailed",
    0x50: "certificateVerificationFailedInvalidTimePeriod",
    0x51: "certificateVerificationFailedInvalidSignature",
    0x52: "certificateVerificationFailedInvalidChainOfTrust",
    0x53: "certificateVerificationFailedInvalidType",
    0x54: "certificateVerificationFailedInvalidFormat",
    0x55: "certificateVerificationFailedInvalidContent",
    0x56: "certificateVerificationFailedInvalidScope",
    0x57: "certificateVerificationFailedInvalidCertificate",
    0x58: "ownershipVerificationFailed",
    0x59: "challengeCalculationFailed",
    0x5A: "settingAccessRightsFailed",
    0x5B: "sessionKeyCreationDerivationFailed",
    0x5C: "configurationDataUsageFailed",
    0x5D: "deAuthenticationFailed",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
    0x81: "rpmTooHigh",
    0x82: "rpmTooLow",
    0x83: "engineIsRunning",
    0x84: "engineIsNotRunning",
    0x85: "engineRunTimeTooLow",
    0x86: "temperatureTooHigh",
    0x87: "temperatureTooLow",
    0x88: "vehicleSpeedTooHigh",
    0x89: "vehicleSpeedTooLow",
    0x8A: "throttlePedalTooHigh",
    0x8B: "throttlePedalTooLow",
    0x8C: "transmissionRangeNotInNeutral",
    0x8D: "transmissionRangeNotInGear",
    0x8F: "brakeSwitchNotClosed",
    0x90: "shifterLeverNotInPark",
    0x91: "torqueConverterClutchLocked",
    0x92: "voltageTooHigh",
    0x93: "voltageTooLow",
    0x94: "resourceTemporarilyNotAvailable",
}


@dataclass
class UdsMessage:
    """UDS报文模型
    
    Attributes:
        service_id: 服务标识符 (SID)
        sub_function: 子功能字节
        data: 数据载荷
        msg_type: 报文类型
        nrc: 负响应码 (仅负响应时有效)
        timestamp: 时间戳
        is_pending: 是否为pending响应 (0x78)
    """
    service_id: int = 0
    sub_function: int = 0
    data: bytes = b""
    msg_type: UdsMessageType = UdsMessageType.REQUEST
    nrc: int = 0
    timestamp: float = field(default_factory=time.time)
    is_pending: bool = False

    @property
    def service_id_hex(self) -> str:
        return f"{self.service_id:02X}"

    @property
    def sub_function_hex(self) -> str:
        return f"{self.sub_function:02X}"

    @property
    def data_hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)

    @property
    def full_data_hex(self) -> str:
        """完整报文HEX（SID + 子功能 + 数据）"""
        parts = [f"{self.service_id:02X}"]
        if self.sub_function:
            parts.append(f"{self.sub_function:02X}")
        if self.data:
            parts.append(self.data_hex)
        return " ".join(parts)

    @property
    def nrc_description(self) -> str:
        """NRC描述"""
        if self.nrc in NRC_CODES:
            return f"0x{self.nrc:02X} ({NRC_CODES[self.nrc]})"
        return f"0x{self.nrc:02X} (unknown)"

    @property
    def is_positive_response(self) -> bool:
        """是否正响应"""
        return self.msg_type == UdsMessageType.POSITIVE_RESPONSE

    @property
    def is_negative_response(self) -> bool:
        """是否负响应"""
        return self.msg_type == UdsMessageType.NEGATIVE_RESPONSE

    @staticmethod
    def parse_response(raw_data: bytes) -> "UdsMessage":
        """解析原始响应数据为UDS报文"""
        if not raw_data:
            return UdsMessage()

        msg = UdsMessage(timestamp=time.time())

        if raw_data[0] == 0x7F:
            # 负响应: 7F <SID> <NRC>
            msg.msg_type = UdsMessageType.NEGATIVE_RESPONSE
            msg.service_id = raw_data[1] if len(raw_data) > 1 else 0
            msg.nrc = raw_data[2] if len(raw_data) > 2 else 0
            if msg.nrc == 0x78:
                msg.is_pending = True
        elif raw_data[0] >= 0x40:
            # 正响应: SID+0x40 <data...>
            msg.msg_type = UdsMessageType.POSITIVE_RESPONSE
            msg.service_id = raw_data[0] - 0x40
            if len(raw_data) > 1:
                msg.sub_function = raw_data[1] & 0x7F  # 去除suppressPosResponse位
            if len(raw_data) > 2:
                msg.data = raw_data[2:]
        else:
            # 请求: SID <data...>
            msg.msg_type = UdsMessageType.REQUEST
            msg.service_id = raw_data[0]
            if len(raw_data) > 1:
                msg.sub_function = raw_data[1]
            if len(raw_data) > 2:
                msg.data = raw_data[2:]

        return msg

    def to_bytes(self) -> bytes:
        """转换为原始字节"""
        result = bytes([self.service_id])
        if self.sub_function:
            result += bytes([self.sub_function])
        if self.data:
            result += self.data
        return result

    def __str__(self) -> str:
        if self.msg_type == UdsMessageType.NEGATIVE_RESPONSE:
            return f"[NRC] 7F {self.service_id_hex} {self.nrc:02X} - {self.nrc_description}"
        elif self.msg_type == UdsMessageType.POSITIVE_RESPONSE:
            return f"[POS] {self.service_id_hex + 0x40:02X} {self.full_data_hex}"
        else:
            return f"[REQ] {self.full_data_hex}"
