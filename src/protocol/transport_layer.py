"""ISO 15765-2 传输层实现

实现UDS报文的分段(Segmentation)和重组(Reassembly)：
- 单帧(SF): <=7字节数据，一帧发送
- 首帧(FF): >7字节数据的第一帧，包含总长度
- 连续帧(CF): 后续数据帧，带序号(0-3循环)
- 流控帧(FC): 接收方的流控响应（BS块大小、STmin最小间隔）
"""

import time
import threading
from typing import Optional, Callable
from src.can_layer.can_interface import CanInterfaceBase
from src.models.can_message import CanMessage, CanMessageType, CanDirection


class FrameType:
    """ISO 15765-2 帧类型"""
    SINGLE_FRAME = 0x00      # 单帧
    FIRST_FRAME = 0x10       # 首帧
    CONSECUTIVE_FRAME = 0x20 # 连续帧
    FLOW_CONTROL = 0x30      # 流控帧


class FlowStatus:
    """流控状态"""
    CONTINUE_TO_SEND = 0x00  # 继续发送
    WAIT = 0x01              # 等待
    OVERFLOW = 0x02          # 溢出


class TransportLayer:
    """ISO 15765-2 传输层
    
    负责将UDS报文分段为CAN帧发送，以及将接收到的CAN帧重组为完整UDS报文。
    支持标准帧(11-bit)和扩展帧(29-bit)。
    """

    def __init__(self, can_interface: CanInterfaceBase,
                 tx_id: int = 0x7E0, rx_id: int = 0x7E8,
                 block_size: int = 0, st_min: int = 0,
                 timeout: float = 2.0):
        """
        Args:
            can_interface: CAN接口实例
            tx_id: 发送CAN ID
            rx_id: 接收CAN ID
            block_size: 流控块大小 (0=不限制)
            st_min: 最小帧间隔 (ms)
            timeout: 超时时间 (秒)
        """
        self._can = can_interface
        self._tx_id = tx_id
        self._rx_id = rx_id
        self._block_size = block_size
        self._st_min = st_min
        self._timeout = timeout
        self._lock = threading.Lock()

        # 接收重组状态
        self._rx_buffer = bytearray()
        self._rx_total_length = 0
        self._rx_expected_seq = 0
        self._rx_receiving = False

    @property
    def tx_id(self) -> int:
        return self._tx_id

    @tx_id.setter
    def tx_id(self, value: int):
        self._tx_id = value

    @property
    def rx_id(self) -> int:
        return self._rx_id

    @rx_id.setter
    def rx_id(self, value: int):
        self._rx_id = value

    def send_tp(self, data: bytes, can_id: int = None) -> bool:
        """发送TP层数据（自动分段）
        
        Args:
            data: 要发送的UDS数据
            can_id: 目标CAN ID（默认使用配置的tx_id）
            
        Returns:
            发送成功返回True
        """
        if can_id is None:
            can_id = self._tx_id

        with self._lock:
            if len(data) <= 7:
                return self._send_single_frame(data, can_id)
            else:
                return self._send_multi_frame(data, can_id)

    def receive_tp(self, timeout: float = None) -> Optional[bytes]:
        """接收TP层数据（自动重组）

        超时语义遭循ISO 15765-2:
        - 等待首帧/单帧(SF/FF): 整体deadline = timeout（P2）
        - 多帧接收中(N_Cr): 每收到一个CF重置deadline，否则真实ECU
          的FF→FC往返+逐帧CF延迟会被整体deadline误杀（自检单帧通过、
          DTC/DID多帧响应全部超时的根因）

        Args:
            timeout: 超时时间（秒）

        Returns:
            完整的UDS数据，超时返回None
        """
        if timeout is None:
            timeout = self._timeout

        start_time = time.time()
        self._rx_buffer = bytearray()
        self._rx_receiving = False

        while time.time() - start_time < timeout:
            msg = self._can.receive(timeout=0.05)
            if msg is None:
                continue

            if msg.can_id != self._rx_id:
                continue

            if not msg.data:
                continue

            frame_type = msg.data[0] & 0xF0

            if frame_type == FrameType.SINGLE_FRAME:
                return self._receive_single_frame(msg.data)

            elif frame_type == FrameType.FIRST_FRAME:
                self._receive_first_frame(msg.data)
                # 发送流控帧
                self._send_flow_control(can_id=self._tx_id)
                # 多帧接收开始: 重置deadline，后续按N_Cr计时
                start_time = time.time()
                continue

            elif frame_type == FrameType.CONSECUTIVE_FRAME:
                result = self._receive_consecutive_frame(msg.data)
                if result is not None:
                    return result
                # 每收到一个CF重置deadline（N_Cr: 相邻CF间隔计时）
                start_time = time.time()
                continue

        return None

    def _send_single_frame(self, data: bytes, can_id: int) -> bool:
        """发送单帧"""
        # PCI(1字节) + 数据(最多7字节)
        pci = len(data) & 0x0F
        frame_data = bytes([pci]) + data
        # 填充到8字节
        frame_data = frame_data + bytes(8 - len(frame_data))
        msg = CanMessage(can_id=can_id, data=frame_data, dlc=8)
        return self._can.send(msg)

    def _send_multi_frame(self, data: bytes, can_id: int) -> bool:
        """发送多帧（首帧 + 连续帧）"""
        total_length = len(data)
        offset = 0

        # 发送首帧 (FF)
        # PCI: FF标识(4bit) + 总长度(12bit)
        ff_pci = 0x10 | ((total_length >> 8) & 0x0F)
        ff_data = bytes([ff_pci, total_length & 0xFF]) + data[:6]
        ff_msg = CanMessage(can_id=can_id, data=ff_data, dlc=8)
        if not self._can.send(ff_msg):
            return False
        offset = 6

        # 等待流控帧 (FC)
        fc_msg = self._can.receive(timeout=self._timeout)
        if fc_msg is None or fc_msg.can_id != self._rx_id:
            return False

        if (fc_msg.data[0] & 0xF0) != FrameType.FLOW_CONTROL:
            return False

        flow_status = fc_msg.data[0] & 0x0F
        if flow_status == FlowStatus.OVERFLOW:
            return False

        bs = fc_msg.data[1]  # Block Size
        st_min = fc_msg.data[2]  # STmin (ms)
        if bs == 0:
            bs = 999999  # 不限制

        # 发送连续帧 (CF)
        seq = 1
        block_count = 0

        while offset < total_length:
            # 检查是否需要等待FC
            if block_count >= bs and offset < total_length:
                fc_msg = self._can.receive(timeout=self._timeout)
                if fc_msg is None:
                    return False
                if (fc_msg.data[0] & 0xF0) == FrameType.FLOW_CONTROL:
                    flow_status = fc_msg.data[0] & 0x0F
                    if flow_status != FlowStatus.CONTINUE_TO_SEND:
                        return False
                    bs = fc_msg.data[1]
                    if bs == 0:
                        bs = 999999
                    st_min = fc_msg.data[2]
                    block_count = 0

            # 构造连续帧
            cf_pci = 0x20 | (seq & 0x0F)
            remaining = total_length - offset
            chunk_size = min(7, remaining)
            cf_data = bytes([cf_pci]) + data[offset:offset + chunk_size]
            cf_data = cf_data + bytes(8 - len(cf_data))  # 填充
            cf_msg = CanMessage(can_id=can_id, data=cf_data, dlc=8)

            if not self._can.send(cf_msg):
                return False

            offset += chunk_size
            seq = (seq + 1) & 0x0F
            block_count += 1

            # STmin延时
            if st_min > 0 and offset < total_length:
                if st_min <= 0x7F:
                    time.sleep(st_min / 1000.0)
                elif 0xF1 <= st_min <= 0xF9:
                    time.sleep((st_min - 0xF0) / 10000.0)

        return True

    def _send_flow_control(self, can_id: int):
        """发送流控帧"""
        fc_data = bytes([
            FrameType.FLOW_CONTROL | FlowStatus.CONTINUE_TO_SEND,
            self._block_size,
            self._st_min,
            0x00, 0x00, 0x00, 0x00, 0x00,
        ])
        msg = CanMessage(can_id=can_id, data=fc_data, dlc=8)
        self._can.send(msg)

    def _receive_single_frame(self, data: bytes) -> bytes:
        """接收单帧"""
        sf_length = data[0] & 0x0F
        return bytes(data[1:1 + sf_length])

    def _receive_first_frame(self, data: bytes):
        """接收首帧"""
        self._rx_total_length = ((data[0] & 0x0F) << 8) | data[1]
        self._rx_buffer = bytearray(data[2:8])
        self._rx_expected_seq = 1
        self._rx_receiving = True

    def _receive_consecutive_frame(self, data: bytes) -> Optional[bytes]:
        """接收连续帧"""
        if not self._rx_receiving:
            return None

        seq = data[0] & 0x0F
        if seq != (self._rx_expected_seq & 0x0F):
            self._rx_receiving = False
            return None

        remaining = self._rx_total_length - len(self._rx_buffer)
        chunk_size = min(7, remaining)
        self._rx_buffer.extend(data[1:1 + chunk_size])
        self._rx_expected_seq = (self._rx_expected_seq + 1) & 0x0F

        if len(self._rx_buffer) >= self._rx_total_length:
            self._rx_receiving = False
            return bytes(self._rx_buffer[:self._rx_total_length])

        return None
