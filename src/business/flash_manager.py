"""刷写流程管理器模块

V2.1: 步骤序列由配置驱动（可按控制器裁剪/增加步骤），支持量产完整流程:
  刷前准备(功能寻址): 扩展会话10 03 → 预编程条件检查31 01 0203 →
      DTC设置OFF 85 02 → 禁止通信 28 03 03
  编程: 编程会话10 02 → 安全访问27 → 驱动下载 → 写指纹2E F15A
  刷写: 擦除31 01 FF00 → APP下载34/36/37
  刷后检查: 完整性校验31 01 0202 → 依赖性校验31 01 FF01 → 复位11 01
  刷后恢复(功能寻址): 扩展会话 → 打开通信28 00 → DTC设置ON 85 01 →
      默认会话10 01 → 清除DTC 14 FF FF FF

支持文件格式: .bin, .s19, .s28, .s37, .hex
"""

import os
import time
import threading
from typing import Optional, Callable
from enum import IntEnum
from src.log.log_manager import get_log_manager
from src.utils.crc32 import crc32


class FlashState(IntEnum):
    IDLE = 0
    ERASING = 1
    REQUESTING_DOWNLOAD = 2
    TRANSFERRING = 3
    EXITING_TRANSFER = 4
    CHECKING = 5
    RESETTING = 6
    COMPLETED = 7
    FAILED = 8
    CANCELLED = 9
    # V2.1: 前置步骤与可选步骤（§9）
    PRECHECKING = 10
    SESSION = 11
    SECURITY = 12
    FINGERPRINT = 13
    DRIVER_DOWNLOAD = 14
    # V2.1: 量产流程扩展步骤（功能寻址前置/后置、例程校验）
    PREPARE = 15          # 刷前准备类步骤（扩展会话/预编程检查/DTC OFF/禁通信）
    CHECK_INTEGRITY = 16  # 完整性校验 31 01 0202
    CHECK_DEPEND = 17     # 依赖性校验 31 01 FF01
    POST = 18             # 刷后恢复类步骤


# 步骤注册表: key -> (显示名称, 说明)
STEP_REGISTRY = {
    "precheck": ("前置检查", "文件解析与校验"),
    "wake": ("ECU 唤醒", "周期发送唤醒报文"),
    "prep_ext_session": ("扩展会话", "功能寻址 0x10 03"),
    "preprog_check": ("预编程条件检查", "0x31 01 0203"),
    "dtc_off": ("DTC设置OFF", "功能寻址 0x85 02"),
    "comm_disable": ("禁止通信", "功能寻址 0x28 03 03"),
    "session": ("编程会话", "0x10 02"),
    "security": ("安全访问", "0x27"),
    "driver": ("驱动下载", "0x34/0x36/0x37 + 0x31 01 0202 (CRC32)"),
    "fingerprint": ("写入指纹", "0x2E"),
    "erase": ("擦除内存", "0x31 01 FF00"),
    "download": ("请求下载", "0x34"),
    "transfer": ("数据传输", "0x36"),
    "transfer_exit": ("传输退出", "0x37"),
    "check_integrity": ("完整性校验", "0x31 01 0202 (CRC32)"),
    "check_dependency": ("依赖性校验", "0x31 01 FF01"),
    "reset": ("ECU复位", "0x11 01"),
    "post_ext_session": ("扩展会话", "功能寻址 0x10 03"),
    "post_comm_enable": ("打开通信", "功能寻址 0x28 00 03"),
    "post_dtc_on": ("DTC设置ON", "功能寻址 0x85 01"),
    "post_default_session": ("默认会话", "功能寻址 0x10 01"),
    "post_clear_dtc": ("清除DTC", "0x14 FF FF FF"),
    "result": ("结果", ""),
}


class FlashFileInfo:
    """刷写文件信息"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        self.file_size = 0
        self.data = b""
        self.format = ""
        # 地址信息（s19/hex可解析；.bin无地址信息保持None）
        self.start_address = None   # 数据最小起始地址
        self.end_address = None     # 数据最大结束地址(不含)
        self._parse_file()

    @property
    def address_span(self) -> Optional[int]:
        """地址跨度（结束-起始），可用作擦除大小；无地址信息返回None"""
        if self.start_address is None or self.end_address is None:
            return None
        return self.end_address - self.start_address

    def _parse_file(self):
        """解析刷写文件"""
        ext = os.path.splitext(self.file_path)[1].lower()
        self.format = ext

        if ext == '.bin':
            self._parse_bin()
        elif ext in ('.s19', '.s28', '.s37'):
            self._parse_srec()
        elif ext == '.hex':
            self._parse_hex()
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

    def _parse_bin(self):
        with open(self.file_path, 'rb') as f:
            self.data = f.read()
        self.file_size = len(self.data)

    def _parse_srec(self):
        """解析S-Record文件"""
        data_segments = []
        min_addr = None
        max_end = None
        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line[0] != 'S':
                    continue
                record_type = int(line[1:2], 16)
                if record_type in (1, 2, 3):  # 数据记录
                    if record_type == 1:
                        addr = int(line[4:8], 16)
                        data_start = 8
                    elif record_type == 2:
                        addr = int(line[4:10], 16)
                        data_start = 10
                    else:
                        addr = int(line[4:12], 16)
                        data_start = 12
                    checksum_len = 2
                    data_hex = line[data_start:len(line) - checksum_len]
                    data_bytes = bytes.fromhex(data_hex)
                    data_segments.append((addr, data_bytes))
                    end = addr + len(data_bytes)
                    if min_addr is None or addr < min_addr:
                        min_addr = addr
                    if max_end is None or end > max_end:
                        max_end = end

        if data_segments:
            data_segments.sort(key=lambda x: x[0])
            self.data = b"".join(seg[1] for seg in data_segments)
            self.file_size = len(self.data)
            self.start_address = min_addr
            self.end_address = max_end

    def _parse_hex(self):
        """解析Intel HEX文件"""
        data_segments = []
        min_addr = None
        max_end = None
        base_addr = 0
        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line[0] != ':':
                    continue
                byte_count = int(line[1:3], 16)
                addr = int(line[3:7], 16)
                record_type = int(line[7:9], 16)
                data_hex = line[9:9 + byte_count * 2]
                data_bytes = bytes.fromhex(data_hex)

                if record_type == 0:  # 数据记录
                    full_addr = base_addr + addr
                    data_segments.append((full_addr, data_bytes))
                    end = full_addr + len(data_bytes)
                    if min_addr is None or full_addr < min_addr:
                        min_addr = full_addr
                    if max_end is None or end > max_end:
                        max_end = end
                elif record_type == 2:  # 扩展段地址
                    base_addr = int.from_bytes(data_bytes, 'big') << 4
                elif record_type == 4:  # 扩展线性地址
                    base_addr = int.from_bytes(data_bytes, 'big') << 16

        if data_segments:
            data_segments.sort(key=lambda x: x[0])
            self.data = b"".join(seg[1] for seg in data_segments)
            self.file_size = len(self.data)
            self.start_address = min_addr
            self.end_address = max_end


class FlashConfig:
    """刷写配置"""

    def __init__(self):
        self.file_path = ""
        self.target_address = 0x08000000
        self.block_size = 1024
        self.erase_address = 0x08000000
        self.erase_size = 0
        # 擦除例程参数格式字节: 部分ECU（如DEM）要求 31 01 FF00 后带
        # 一个格式字节声明参数布局——0x44=高半字节地址长度4 + 低半字节大小长度4，
        # 即 31 01 FF00 44 + 地址(4B) + 大小(4B)；设为0则不带格式字节
        self.erase_format_byte = 0x44
        # 擦除块大小: 擦除一般按整块进行，擦除大小向上取整到该值的整数倍（默认0x40000）
        self.erase_block_size = 0x40000
        # 可选步骤 —— 擦除内存(0x31 01 FF00): 部分ECU在请求下载(0x34)时
        # 自动擦除，或调试场景不希望破坏原有数据，可关闭
        self.erase_memory = True
        # 压测: 连续刷写轮数（>1时单轮失败记录后继续，结束后输出汇总）
        self.repeat_count = 1
        self.security_level = 9          # 0=跳过安全访问（量产流程常用 9，即 27 09/0A）
        self.key_generator = None        # Callable[[int, bytes], bytes] 密钥钩子 (level, seed)->key
        self.enter_programming = True    # 刷写前切换编程会话(0x10 02)
        # 编程会话(10 02)切换后的跳转稳定等待秒数: ECU需Bootloader跳转/
        # 编程环境初始化，立即发后续请求会被拒（7F 7F），实测DEM需2s
        self.prog_session_delay = 2.0
        # ECU复位(11 01)后的重启稳定等待秒数
        self.reset_delay = 2.0
        self.reset_after_flash = True
        # V2.1: 可选步骤 —— 写入指纹（部分控制器要求，如 0x2E F15A）
        self.write_fingerprint = True
        self.fingerprint_did = 0xF15A
        self.fingerprint_data = b""
        # V2.1: 可选步骤 —— 刷写驱动下载（flash driver，先于擦除与APP下载）
        self.driver_path = ""
        self.driver_address = 0x20000000
        self.driver_block_size = 0x1000
        # V2.1: 功能寻址（刷前准备/刷后恢复步骤广播发送，标准0x7DF，
        # 可在ECU定义ecu.json的functional_tx_id中覆盖）
        self.functional_tx_id = 0x7DF
        # V2.1: 刷前准备（功能寻址）
        self.prep_ext_session = True         # 10 03 扩展会话
        self.preprog_check = True            # 31 01 0203 预编程条件检查(物理寻址)
        self.dtc_off = True                  # 85 02 DTC设置OFF
        self.comm_disable = True             # 28 03 03 禁止非诊断报文收发
        # V2.1: 刷后检查例程（物理寻址）
        self.check_integrity = True          # 31 01 0202 完整性校验
        self.check_dependency = True         # 31 01 FF01 依赖性校验
        # V2.1: 刷后恢复（功能寻址）
        self.post_ext_session = True         # 10 03 扩展会话
        self.post_comm_enable = True         # 28 00 03 打开通信
        self.post_dtc_on = True              # 85 01 DTC设置ON
        self.post_default_session = True     # 10 01 默认会话
        self.post_clear_dtc = True           # 14 FF FF FF 清除 DTC
        # V2.2: ECU 唤醒配置（部分控制器需要先发送唤醒报文才能进行诊断）
        self.wake_enabled = False            # 是否启用唤醒
        self.wake_can_id = 0x160             # 唤醒报文 CAN ID
        self.wake_data = bytes([0xFF] * 8)   # 唤醒报文数据
        self.wake_period = 0.05              # 唤醒周期 (秒)
        self.wake_duration = 3.0             # 唤醒持续时间 (秒)


class FlashProgress:
    """刷写进度"""

    def __init__(self):
        self.state = FlashState.IDLE
        self.total_bytes = 0
        self.transferred_bytes = 0
        self.current_block = 0
        self.total_blocks = 0
        self.start_time = 0.0
        self.elapsed_time = 0.0
        self.error_message = ""
        self.transfer_kind = "app"       # "app"=应用传输(计入进度) "driver"=驱动传输
        self.repeat_current = 1          # 压测当前轮次（刷写线程写入，GUI读取）
        self.repeat_total = 1            # 压测总轮次

    @property
    def percentage(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return self.transferred_bytes / self.total_bytes * 100

    @property
    def transfer_rate(self) -> float:
        if self.elapsed_time <= 0:
            return 0.0
        return self.transferred_bytes / self.elapsed_time / 1024  # KB/s

    @property
    def remaining_time(self) -> float:
        if self.transfer_rate <= 0:
            return 0.0
        remaining = self.total_bytes - self.transferred_bytes
        return remaining / (self.transfer_rate * 1024)


class FlashManager:
    """刷写流程管理器（步骤序列由配置驱动）"""

    # 步骤 key -> FlashState
    _STEP_STATES = {
        "precheck": FlashState.PRECHECKING,
        "wake": FlashState.PRECHECKING,
        "prep_ext_session": FlashState.PREPARE,
        "preprog_check": FlashState.PREPARE,
        "dtc_off": FlashState.PREPARE,
        "comm_disable": FlashState.PREPARE,
        "session": FlashState.SESSION,
        "security": FlashState.SECURITY,
        "driver": FlashState.DRIVER_DOWNLOAD,
        "fingerprint": FlashState.FINGERPRINT,
        "erase": FlashState.ERASING,
        "download": FlashState.REQUESTING_DOWNLOAD,
        "transfer": FlashState.TRANSFERRING,
        "transfer_exit": FlashState.EXITING_TRANSFER,
        "check_integrity": FlashState.CHECK_INTEGRITY,
        "check_dependency": FlashState.CHECK_DEPEND,
        "reset": FlashState.RESETTING,
        "post_ext_session": FlashState.POST,
        "post_comm_enable": FlashState.POST,
        "post_dtc_on": FlashState.POST,
        "post_default_session": FlashState.POST,
        "post_clear_dtc": FlashState.POST,
    }

    def __init__(self, uds_client=None):
        self._uds_client = uds_client
        self._config = FlashConfig()
        self._progress = FlashProgress()
        self._running = False
        self._cancelled = False
        self._thread: Optional[threading.Thread] = None
        self._logger = get_log_manager().get_flash_logger()
        self._progress_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None
        self._last_fail_key = ""        # 最近一轮失败步骤（压测汇总终态用）
        self._last_fail_name = ""

    @property
    def config(self) -> FlashConfig:
        return self._config

    @config.setter
    def config(self, value: FlashConfig):
        self._config = value

    @property
    def progress(self) -> FlashProgress:
        return self._progress

    @property
    def state(self) -> FlashState:
        return self._progress.state

    @property
    def is_running(self) -> bool:
        return self._running

    def set_callbacks(self, progress_cb: Callable = None, state_cb: Callable = None):
        """设置回调函数"""
        self._progress_callback = progress_cb
        self._state_callback = state_cb

    # ---------------- 步骤序列 ----------------

    @staticmethod
    def steps(config: FlashConfig) -> list:
        """根据配置生成实际执行的步骤序列 [(key, 名称，说明), ...]
    
        面板据此渲染步骤列表，保证与执行顺序完全一致。
        """
        seq = ["precheck"]
        # ECU 唤醒（部分控制器需要先唤醒才能诊断）
        if config.wake_enabled:
            seq.append("wake")
        # 刷前准备（功能寻址）
        if config.prep_ext_session:
            seq.append("prep_ext_session")
        if config.preprog_check:
            seq.append("preprog_check")
        if config.dtc_off:
            seq.append("dtc_off")
        if config.comm_disable:
            seq.append("comm_disable")
        # 编程
        if config.enter_programming:
            seq.append("session")
        if config.security_level > 0:
            seq.append("security")
        if config.driver_path:
            seq.append("driver")
        if config.write_fingerprint:
            seq.append("fingerprint")
        # 刷写主体（擦除可选: 部分ECU请求下载时自动擦除）
        if config.erase_memory:
            seq.append("erase")
        seq += ["download", "transfer", "transfer_exit"]
        # 刷后检查与复位
        if config.check_integrity:
            seq.append("check_integrity")
        if config.check_dependency:
            seq.append("check_dependency")
        if config.reset_after_flash:
            seq.append("reset")
        # 刷后恢复（功能寻址）
        if config.post_ext_session:
            seq.append("post_ext_session")
        if config.post_comm_enable:
            seq.append("post_comm_enable")
        if config.post_dtc_on:
            seq.append("post_dtc_on")
        if config.post_default_session:
            seq.append("post_default_session")
        if config.post_clear_dtc:
            seq.append("post_clear_dtc")
        seq.append("result")
        return [(k,) + STEP_REGISTRY[k] for k in seq]

    # ---------------- 刷写控制 ----------------

    def start_flash(self, config: FlashConfig = None):
        """开始刷写（异步）"""
        if self._running:
            return

        if config:
            self._config = config

        self._cancelled = False
        self._thread = threading.Thread(target=self._flash_thread, daemon=True)
        self._thread.start()

    def stop_flash(self):
        """请求停止刷写

        仅置取消标志，由刷写线程在最近的取消检查点（步骤边界/块间）
        退出并发出 CANCELLED 终态；_running 由线程 finally 统一复位，
        避免停止后 is_running 提前变 False 导致状态判断错乱。
        """
        self._cancelled = True

    def _set_state(self, state: FlashState, error_msg: str = "", key: str = ""):
        self._progress.state = state
        if error_msg:
            self._progress.error_message = error_msg
        if self._state_callback:
            self._state_callback(state, error_msg, key)

    def _flash_thread(self):
        """刷写线程: 按配置驱动的步骤序列依次执行（支持多轮压测）"""
        self._running = True
        repeat = max(1, int(getattr(self._config, "repeat_count", 1) or 1))
        ok_count = fail_count = 0

        handlers = {
            "precheck": self._step_precheck,
            "wake": self._step_wake,
            "prep_ext_session": self._step_prep_ext_session,
            "preprog_check": self._step_preprog_check,
            "dtc_off": self._step_dtc_off,
            "comm_disable": self._step_comm_disable,
            "session": self._step_programming_session,
            "security": self._step_security_access,
            "driver": self._step_driver_download,
            "fingerprint": self._step_write_fingerprint,
            "erase": self._step_erase,
            "download": self._step_request_download,
            "transfer": self._step_transfer_data,
            "transfer_exit": self._step_transfer_exit,
            "check_integrity": self._step_check_integrity,
            "check_dependency": self._step_check_dependency,
            "reset": self._step_reset,
            "post_ext_session": self._step_post_ext_session,
            "post_comm_enable": self._step_post_comm_enable,
            "post_dtc_on": self._step_post_dtc_on,
            "post_default_session": self._step_post_default_session,
            "post_clear_dtc": self._step_post_clear_dtc,
        }

        try:
            for rnd in range(1, repeat + 1):
                if self._cancelled:
                    break
                # 每轮重置进度（进度条/耗时归零重新累计）
                self._progress = FlashProgress()
                self._progress.start_time = time.time()
                self._progress.repeat_current = rnd
                self._progress.repeat_total = repeat
                if repeat > 1:
                    self._logger.info(
                        f"=== 压测第 {rnd}/{repeat} 轮开始 ===")
                result = self._run_one_flash(handlers)
                if result == "completed":
                    ok_count += 1
                elif result == "cancelled":
                    break
                else:
                    fail_count += 1
                if rnd < repeat:
                    time.sleep(1.0)  # 轮间间隔: 等ECU完成复位/通信栈恢复

            # 统一终态（单轮模式与原行为一致）
            if self._cancelled:
                self._set_state(FlashState.CANCELLED, "", "")
            elif fail_count:
                if repeat > 1:
                    msg = (f"压测汇总: {ok_count}成功/{fail_count}失败"
                           f"/共{repeat}轮")
                else:
                    msg = f"{self._last_fail_name}失败"
                self._set_state(FlashState.FAILED, msg,
                                self._last_fail_key)
            else:
                summary = (f"压测汇总: {ok_count}/{repeat}轮全部成功"
                           if repeat > 1 else "")
                self._set_state(FlashState.COMPLETED, summary, "result")
            if repeat > 1:
                self._logger.info(
                    f"=== 压测结束: {ok_count}成功/{fail_count}失败"
                    f"/共{repeat}轮 ===")
        finally:
            self._running = False

    def _run_one_flash(self, handlers) -> str:
        """执行单轮刷写步骤序列（不发送终态，由压测外层统一上报）

        Returns: "completed" / "failed" / "cancelled"
        """
        try:
            self._logger.info(f"文件: {self._config.file_path}")

            for key, name, _desc in self.steps(self._config):
                if key == "result":
                    break
                if self._cancelled:
                    return "cancelled"
                self._set_state(self._STEP_STATES[key], "", key)
                self._logger.info(f"[{name}] 开始")
                # 返回: True=成功, False=失败, None=按配置跳过
                ret = handlers[key]()
                if ret is None:
                    self._logger.info(f"[{name}] 跳过")
                    continue
                if not ret:
                    self._logger.error(f"[{name}] 失败")
                    self._last_fail_key = key
                    self._last_fail_name = name
                    return "failed"
                self._logger.info(f"[{name}] OK")

            total_time = time.time() - self._progress.start_time
            self._logger.info(
                f"=== 单轮刷写成功 === 耗时: {total_time:.1f}s")
            return "completed"

        except Exception as e:
            # 不发终态: 压测中间轮的异常由外层统一汇总上报
            self._logger.error(f"刷写异常: {e}")
            self._last_fail_key = ""
            self._last_fail_name = f"异常({e})"
            return "failed"

    # ---------------- 功能寻址 ----------------

    def _send_functional(self, data: bytes) -> bool:
        """功能寻址发送: 临时切换发送ID为功能地址，发送后恢复物理地址

        不抑制正响应，等待正响应 (SID+0x40)。
        """
        if not self._uds_client:
            self._logger.info(f"  模拟功能寻址: {data.hex(' ').upper()}")
            return True
        client = self._uds_client
        saved_tx = client.tx_id
        client.tx_id = self._config.functional_tx_id
        try:
            self._logger.info(f"  功能寻址: {data.hex(' ').upper()}")
            resp = client.send_raw(data)
            return resp is not None and resp[0] == data[0] + 0x40
        finally:
            client.tx_id = saved_tx

    # ---------------- 各步骤实现 ----------------

    def _step_precheck(self) -> bool:
        """前置检查: 文件存在/可解析/非空；配置了驱动文件时一并解析"""
        try:
            self._file_info = FlashFileInfo(self._config.file_path)
        except Exception as e:
            self._progress.error_message = f"文件校验失败: {e}"
            self._logger.error(f"文件校验失败: {e}")
            return False
        if not self._file_info.data:
            self._progress.error_message = "文件内容为空"
            return False
        self._progress.total_bytes = self._file_info.file_size
        self._logger.info(
            f"  应用文件: 格式{self._file_info.format}, "
            f"大小{self._file_info.file_size} bytes")

        self._driver_info = None
        if self._config.driver_path:
            try:
                self._driver_info = FlashFileInfo(self._config.driver_path)
                if not self._driver_info.data:
                    self._progress.error_message = "驱动文件内容为空"
                    return False
                self._logger.info(
                    f"  驱动文件: {self._driver_info.file_name}, "
                    f"大小{self._driver_info.file_size} bytes")
            except Exception as e:
                self._progress.error_message = f"驱动文件校验失败: {e}"
                self._logger.error(f"驱动文件校验失败: {e}")
                return False
        return True

    def _step_wake(self) -> bool:
        """ECU 唤醒：周期发送唤醒报文，持续指定时间

        部分控制器（如 DEM）在休眠状态下需要先发送唤醒报文才能进行诊断。
        通过 UDS 客户端的底层 CAN 接口直接发送原始 CAN 帧。
        """
        if not self._config.wake_enabled:
            return True
        if not self._uds_client:
            self._logger.info("  模拟 ECU 唤醒（无真实 CAN 接口）")
            return True
        # 获取底层 CAN 接口
        can_iface = getattr(self._uds_client, '_can', None)
        if can_iface is None:
            self._progress.error_message = "无法获取 CAN 接口，无法发送唤醒报文"
            self._logger.error(self._progress.error_message)
            return False
        can_id = self._config.wake_can_id
        data = self._config.wake_data
        period = self._config.wake_period
        duration = self._config.wake_duration
        self._logger.info(
            f"  唤醒：CAN ID=0x{can_id:03X} 数据={data.hex(' ').upper()} "
            f"周期={period*1000:.0f}ms 持续时间={duration}s")
        # 周期发送唤醒报文
        from src.models.can_message import CanMessage
        start_time = time.time()
        sent_count = 0
        try:
            while time.time() - start_time < duration:
                if self._cancelled:
                    return False
                msg = CanMessage(can_id=can_id, data=data, dlc=8)
                if can_iface.send(msg):
                    sent_count += 1
                time.sleep(period)
        except Exception as e:
            self._progress.error_message = f"唤醒发送异常：{e}"
            self._logger.error(self._progress.error_message)
            return False
        self._logger.info(f"  唤醒完成：共发送 {sent_count} 帧")
        # 唤醒后等待 ECU 完全启动
        time.sleep(0.5)
        return True

    def _step_prep_ext_session(self) -> bool:
        """刷前准备: 功能寻址进入扩展会话 (10 03)

        部分控制器不响应标准功能地址0x7DF（私有网络功能地址不同），
        功能寻址无正响应时回退物理寻址重试，避免单 ECU 刷写被阻塞。
        """
        if self._send_functional(bytes([0x10, 0x03])):
            return True
        self._logger.info("  功能寻址无正响应，回退物理寻址 10 03 重试")
        if not self._uds_client:
            return True
        resp = self._uds_client.diagnostic_session_control(0x03)
        ok = resp is not None and resp[0] == 0x50
        if ok:
            self._logger.info("  物理寻址 10 03 成功")
        return ok

    def _step_preprog_check(self) -> bool:
        """预编程条件检查: 例程 31 01 0203（物理寻址，需等待检查结果）"""
        if not self._uds_client:
            return True
        resp = self._uds_client.routine_control(0x01, 0x0203)
        return resp is not None and resp[0] == 0x71

    def _step_dtc_off(self) -> bool:
        """功能寻址 DTC设置OFF (85 02)"""
        return self._send_functional(bytes([0x85, 0x02]))

    def _step_comm_disable(self) -> bool:
        """功能寻址通信控制: 禁止非诊断报文收发 (28 03 03)"""
        return self._send_functional(bytes([0x28, 0x03, 0x03]))

    def _step_programming_session(self) -> bool:
        """切换编程会话 (0x10 02)

        部分ECU（如DEM）切到编程会话后需内部初始化（Bootloader跳转、
        编程环境准备），立即发后续请求会被拒（7F 7F 
        serviceNotSupportedInActiveSession），切换后需等待稳定。
        """
        if not self._uds_client:
            return True  # 模拟模式
        resp = self._uds_client.diagnostic_session_control(0x02)
        ok = resp is not None and resp[0] == 0x50
        if ok and self._config.prog_session_delay > 0:
            self._logger.info(
                f"  编程会话已切换，等待ECU跳转初始化稳定 "
                f"({self._config.prog_session_delay:g}s)")
            time.sleep(self._config.prog_session_delay)
        return ok

    def _step_security_access(self) -> bool:
        """安全访问: 请求种子 -> 计算密钥 -> 发送密钥

        会话类NRC(7F/12/22)时ECU可能尚未就绪，等待后重试一次。
        """
        if not self._uds_client:
            return True  # 模拟模式
        if self._config.key_generator is None:
            self._logger.error(
                f"未配置Level {self._config.security_level}的密钥算法，无法解锁")
            return False
        level = self._config.security_level
        resp = None
        for attempt in (1, 2):
            resp = self._uds_client.security_access_request_seed(level)
            if resp and resp[0] == 0x67:
                break
            retryable = (resp is not None and len(resp) >= 3
                         and resp[0] == 0x7F
                         and resp[2] in (0x7F, 0x12, 0x22))
            if retryable and attempt == 1:
                self._logger.info(
                    f"  请求种子被拒(NRC=0x{resp[2]:02X})，"
                    "等待500ms后重试")
                time.sleep(0.5)
            else:
                return False
        if not resp or resp[0] != 0x67:
            return False
        seed = resp[2:]
        if not any(seed):  # 全零种子 = 已解锁
            return True
        try:
            key = self._config.key_generator(level, seed)
        except Exception as e:
            self._logger.error(f"密钥计算异常: {e}")
            return False
        if not key:
            return False
        resp = self._uds_client.security_access_send_key(level, bytes(key))
        return resp is not None and resp[0] == 0x67

    def _step_write_fingerprint(self) -> Optional[bool]:
        """写入指纹 (0x2E, WriteDataByIdentifier)

        部分控制器要求在擦除前写入刷写指纹（刷写日期/测试仪标识等）。
        """
        if not self._config.fingerprint_data:
            self._progress.error_message = "已启用写入指纹但未提供指纹数据"
            return False
        if not self._uds_client:
            self._logger.info(
                f"  模拟写入指纹: DID 0x{self._config.fingerprint_did:04X}, "
                f"{self._config.fingerprint_data.hex().upper()}")
            return True
        resp = self._uds_client.write_data_by_identifier(
            self._config.fingerprint_did, self._config.fingerprint_data)
        return resp is not None and resp[0] == 0x6E

    def _step_erase(self) -> bool:
        """擦除内存: 例程控制 EraseMemory (0xFF00)

        擦除按整块进行: 擦除大小向上取整到 erase_block_size 的整数倍。
        """
        if not self._uds_client:
            return True  # 模拟模式
        size = self._config.erase_size or self._progress.total_bytes
        bs = self._config.erase_block_size
        if bs > 0:
            size = (size + bs - 1) // bs * bs
        self._logger.info(
            f"  擦除: 0x{self._config.erase_address:08X} +0x{size:X}")
        # 格式字节（如0x44）声明后续参数布局: 地址4字节 + 大小4字节
        fmt = bytes([self._config.erase_format_byte]) \
            if self._config.erase_format_byte else b""
        resp = self._uds_client.routine_control(
            0x01, 0xFF00,
            fmt + self._config.erase_address.to_bytes(4, 'big') +
            size.to_bytes(4, 'big'))
        return resp is not None and resp[0] == 0x71

    def _step_driver_download(self) -> bool:
        """刷写驱动下载: RequestDownload → TransferData → TransferExit → 完整性校验

        部分控制器要求先下载 flash driver 到RAM，再由驱动执行应用擦除/写入。
        驱动通常下载到RAM，无需擦除；传输完成后用 31 01 0202 (CRC32) 校验驱动完整性。
        """
        if self._driver_info is None:
            return True
        self._progress.transfer_kind = "driver"
        if not self._uds_client:
            self._logger.info(
                f"  模拟驱动下载: {self._driver_info.file_size} bytes -> "
                f"0x{self._config.driver_address:08X}")
            self._progress.transfer_kind = "app"
            return True
        max_len = self._request_download(
            self._config.driver_address, self._driver_info.file_size)
        if max_len is None:
            return False
        # maxNumberOfBlockLength是整条TransferData请求的上限（含36+块序号
        # 共2字节头），纯数据块大小需减去头部，否则单条请求超出ECU声明上限
        block_size = min(self._config.driver_block_size, max(1, max_len - 2))
        self._logger.info(f"  驱动块大小 {block_size} (maxBlockLength={max_len})")
        if not self._transfer_blocks(self._driver_info.data, block_size):
            return False
        resp = self._uds_client.request_transfer_exit()
        if resp is None or resp[0] != 0x77:
            return False
        # 驱动完整性校验: 31 01 0202 (CRC32)
        ok = self._check_integrity(self._driver_info.data, "驱动")
        self._progress.transfer_kind = "app"
        return ok

    def _step_request_download(self) -> bool:
        """请求下载（应用文件）"""
        self._max_block_len = self._request_download(
            self._config.target_address, self._file_info.file_size)
        if self._max_block_len is None:
            return False
        self._logger.info(f"  maxBlockLength={self._max_block_len}")
        return True

    def _step_transfer_data(self) -> bool:
        """分块传输应用数据"""
        # 同驱动传输: maxNumberOfBlockLength含36+块序号2字节头，数据块需减头
        block_size = min(self._config.block_size, max(1, self._max_block_len - 2))
        self._progress.total_blocks = (
            self._file_info.file_size + block_size - 1) // block_size
        self._logger.info(
            f"  块大小 {block_size}, 共 {self._progress.total_blocks} 块")
        ok = self._transfer_blocks(self._file_info.data, block_size)
        if ok:
            elapsed = time.time() - self._progress.start_time
            self._logger.info(
                f"  全部传输完成 ({self._progress.total_blocks} blocks, "
                f"耗时 {elapsed:.2f}s, 速率 {self._progress.transfer_rate:.2f} KB/s)")
        return ok

    def _step_transfer_exit(self) -> bool:
        """传输退出 (0x37)"""
        if not self._uds_client:
            return True
        resp = self._uds_client.request_transfer_exit()
        return resp is not None and resp[0] == 0x77

    def _step_check_integrity(self) -> bool:
        """完整性校验: 例程 31 01 0202，校验算法CRC32（APP数据）"""
        if not self._uds_client:
            return True
        app_info = getattr(self, "_file_info", None)
        return self._check_integrity(app_info.data if app_info else b"", "APP")

    def _check_integrity(self, data: bytes, label: str) -> bool:
        """完整性校验公共逻辑: 例程 31 01 0202，校验算法CRC32

        CRC32参数: 多项式0x04C11DB7，初始值0xFFFFFFFF，
        输入/输出反转，结果与0xFFFFFFFF异或（标准校验向量 31..39 -> 0xCBF43926）。
        本地先算CRC，随例程选项记录下发供ECU比对；
        若ECU响应中回带4字节校验和，再与本地值交叉核对。
        """
        option = b""
        local_crc = None
        if data:
            local_crc = crc32(data)
            option = local_crc.to_bytes(4, "big")
            self._logger.info(f"  {label}本地CRC32: 0x{local_crc:08X}")
        resp = self._uds_client.routine_control(0x01, 0x0202, option)
        if resp is None or resp[0] != 0x71:
            self._logger.error(f"  {label}完整性校验例程失败")
            return False
        # 响应 71 01 0202 + 可选4字节校验和，回带时与本地交叉核对
        if local_crc is not None and len(resp) >= 8:
            ecu_crc = int.from_bytes(resp[4:8], "big")
            if ecu_crc != local_crc:
                self._logger.error(
                    f"  {label}完整性校验不一致: ECU=0x{ecu_crc:08X} "
                    f"本地=0x{local_crc:08X}")
                return False
            self._logger.info(f"  {label}ECU侧校验和与本地一致")
        return True

    def _step_check_dependency(self) -> bool:
        """依赖性校验: 例程 31 01 FF01"""
        if not self._uds_client:
            return True
        resp = self._uds_client.routine_control(0x01, 0xFF01)
        return resp is not None and resp[0] == 0x71

    def _step_reset(self) -> bool:
        """ECU复位 (0x11 01)，复位后等待重启稳定"""
        if self._uds_client:
            self._uds_client.ecu_reset(0x01)
            if self._config.reset_delay > 0:
                self._logger.info(
                    f"  ECU复位已发送，等待重启稳定 "
                    f"({self._config.reset_delay:g}s)")
                time.sleep(self._config.reset_delay)
        return True

    # ---------------- 刷后恢复（功能寻址） ----------------

    def _step_post_ext_session(self) -> bool:
        """刷后恢复: 功能寻址进入扩展会话 (10 03)，失败回退物理寻址"""
        if self._send_functional(bytes([0x10, 0x03])):
            return True
        self._logger.info("  功能寻址无正响应，回退物理寻址 10 03 重试")
        if not self._uds_client:
            return True
        resp = self._uds_client.diagnostic_session_control(0x03)
        return resp is not None and resp[0] == 0x50

    def _step_post_comm_enable(self) -> bool:
        """刷后恢复: 功能寻址打开通信 (28 00 03)"""
        return self._send_functional(bytes([0x28, 0x00, 0x03]))

    def _step_post_dtc_on(self) -> bool:
        """刷后恢复: 功能寻址 DTC设置ON (85 01)"""
        return self._send_functional(bytes([0x85, 0x01]))

    def _step_post_default_session(self) -> bool:
        """刷后恢复: 功能寻址切回默认会话 (10 01)"""
        return self._send_functional(bytes([0x10, 0x01]))

    def _step_post_clear_dtc(self) -> bool:
        """刷后恢复: 清除DTC (14 FF FF FF)"""
        if not self._uds_client:
            return True
        resp = self._uds_client.clear_dtc(0xFFFFFF)
        return resp is not None and resp[0] == 0x54

    # ---------------- 底层传输 ----------------

    def _request_download(self, address: int, size: int) -> Optional[int]:
        """RequestDownload，返回maxBlockLength，失败返回None"""
        if not self._uds_client:
            return 1024  # 模拟模式
        resp = self._uds_client.request_download(address, size)
        if resp and len(resp) >= 3 and resp[0] == 0x74:
            # 解析maxNumberOfBlockLength
            length_format = resp[1] >> 4
            max_len_bytes = resp[2:2 + length_format]
            return int.from_bytes(max_len_bytes, 'big')
        return None

    def _transfer_blocks(self, data: bytes, block_size: int) -> bool:
        """分块传输数据；应用传输更新进度，驱动传输仅计数"""
        is_app = self._progress.transfer_kind == "app"
        offset = 0
        block_seq = 1

        while offset < len(data):
            if self._cancelled:
                return False

            chunk = data[offset:offset + block_size]
            if self._uds_client:
                resp = self._uds_client.transfer_data(block_seq, chunk)
                if not resp or resp[0] != 0x76:
                    self._logger.error(f"Block {block_seq} 传输失败")
                    return False

            offset += len(chunk)
            if is_app:
                self._progress.transferred_bytes = offset
                self._progress.current_block = block_seq
                self._progress.elapsed_time = time.time() - self._progress.start_time
                if self._progress_callback:
                    self._progress_callback(self._progress)

            block_seq = (block_seq + 1) & 0xFF

        return True
