"""DTC管理与状态解析模块

功能：
- 多种读取模式（0x19子功能02/01/04/06/0A）
- DTC状态8位可视化（符合ISO 14229状态位定义）
- 快照数据/扩展数据读取
- DTC严重度分级
- DTC时间线记录
"""

import time
import json
from typing import Optional
from src.log.log_manager import get_log_manager


# DTC状态位定义（ISO 14229）
DTC_STATUS_BITS = {
    0: "testFailed",               # 位0: 测试失败
    1: "testFailedThisOperationCycle",  # 位1: 本操作周期测试失败
    2: "pendingDtc",               # 位2: 待确认DTC
    3: "confirmedDtc",             # 位3: 已确认DTC
    4: "testNotCompletedSinceLastClear",  # 位4: 上次清除后未完成测试
    5: "testFailedSinceLastClear",  # 位5: 上次清除后测试失败
    6: "testNotCompletedThisOperationCycle",  # 位6: 本操作周期未完成测试
    7: "warningIndicatorRequested",  # 位7: 警告指示灯请求
}


class DtcRecord:
    """DTC记录"""

    def __init__(self, dtc_id: int, status: int, occurrence: int = 0,
                 definition: str = "", severity: str = ""):
        self.dtc_id = dtc_id
        self.status = status
        self.occurrence = occurrence
        self.definition = definition
        self.severity = severity
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.snapshot_data: Optional[bytes] = None
        self.extended_data: Optional[bytes] = None

    @property
    def dtc_id_hex(self) -> str:
        return f"0x{self.dtc_id:06X}"

    @property
    def status_bits(self) -> dict[int, bool]:
        """获取8位状态"""
        return {i: bool(self.status & (1 << i)) for i in range(8)}

    @property
    def status_description(self) -> str:
        """状态描述"""
        active = [DTC_STATUS_BITS[i] for i in range(8) if self.status & (1 << i)]
        return ", ".join(active) if active else "无激活状态"

    @property
    def status_bits_label(self) -> str:
        """激活位号简写（如 'Bit4 Bit6'，详细含义见状态位区）"""
        bits = [f"Bit{i}" for i in range(8) if self.status & (1 << i)]
        return " ".join(bits) if bits else "无"

    @property
    def is_confirmed(self) -> bool:
        """是否已确认DTC"""
        return bool(self.status & 0x08)

    @property
    def is_pending(self) -> bool:
        """是否待确认DTC"""
        return bool(self.status & 0x04)

    @property
    def is_history(self) -> bool:
        """是否历史DTC（测试失败但非当前确认）"""
        return bool(self.status & 0x20) and not self.is_confirmed

    @property
    def severity_level(self) -> str:
        """严重度级别"""
        if self.is_confirmed:
            return "confirmed"  # 红色
        elif self.is_pending:
            return "pending"  # 橙色
        elif self.is_history:
            return "history"  # 灰色
        return "unknown"

    def to_dict(self) -> dict:
        return {
            "dtc_id": self.dtc_id,
            "status": self.status,
            "occurrence": self.occurrence,
            "definition": self.definition,
            "status_description": self.status_description,
            "severity_level": self.severity_level,
        }


class DtcTimelineEntry:
    """DTC时间线条目"""

    def __init__(self, dtc_id: int, event: str, status: int):
        self.timestamp = time.time()
        self.dtc_id = dtc_id
        self.event = event  # "appeared", "disappeared", "status_changed"
        self.status = status


class DtcManager:
    """DTC管理器"""

    def __init__(self):
        self._dtc_definitions: dict[int, str] = {}  # dtc_id -> 描述
        self._current_dtcs: dict[int, DtcRecord] = {}  # dtc_id -> record
        self._timeline: list[DtcTimelineEntry] = []
        self._dtc_count = 0
        self._logger = get_log_manager().get_app_logger()

    @property
    def current_dtcs(self) -> dict[int, DtcRecord]:
        return dict(self._current_dtcs)

    @property
    def timeline(self) -> list[DtcTimelineEntry]:
        return list(self._timeline)

    @property
    def dtc_count(self) -> int:
        return len(self._current_dtcs)

    def load_definitions(self, file_path: str) -> int:
        """加载DTC定义文件（JSON格式: 纯列表或含dtcs键的对象）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, dict):
                data = data.get("dtcs", [])
            return self.load_definitions_from_list(data)

        except Exception as e:
            self._logger.error(f"加载DTC定义失败: {e}")
            return 0

    def load_definitions_from_list(self, items: list) -> int:
        """从DTC定义字典列表加载（dtc_id/description）"""
        count = 0
        for item in items:
            dtc_id = item.get("dtc_id", "")
            if isinstance(dtc_id, str):
                dtc_id = int(dtc_id, 16)
            desc = item.get("description", "")
            self._dtc_definitions[dtc_id] = desc
            count += 1

        self._logger.info(f"加载了 {count} 个DTC定义")
        return count

    def get_definition(self, dtc_id: int) -> str:
        """查询单个DTC的描述定义"""
        return self._dtc_definitions.get(dtc_id, "")

    def refresh_current_definitions(self):
        """定义更新后回填当前记录描述（已读取的DTC立即显示名称）"""
        for record in self._current_dtcs.values():
            record.definition = self._dtc_definitions.get(record.dtc_id, "")

    def parse_read_dtc_response(self, response: bytes) -> list[DtcRecord]:
        """解析0x19读取DTC响应

        Args:
            response: UDS响应数据（如 59 02 FF ...）

        Returns:
            DTC记录列表
        """
        records = []

        if not response or len(response) < 3:
            return records

        sid = response[0]
        if sid != 0x59:
            return records

        sub_func = response[1]

        if sub_func == 0x02 or sub_func == 0x0A:
            # 按状态掩码读取DTC / 读取所有DTC
            # 格式: 59 02 FF [DTC1(3bytes) + Status(1byte)] [DTC2...]
            data = response[3:]  # 跳过 sub_func 和 DTCFormatIdentifier
            i = 0
            while i + 3 < len(data):
                dtc_id = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
                status = data[i + 3] if i + 3 < len(data) else 0
                i += 4

                definition = self._dtc_definitions.get(dtc_id, "")
                record = DtcRecord(dtc_id, status, definition=definition)

                # 更新时间线
                self._update_timeline(dtc_id, status)
                records.append(record)

            # 更新当前DTC列表
            new_dtc_ids = {r.dtc_id for r in records}
            for dtc_id in list(self._current_dtcs.keys()):
                if dtc_id not in new_dtc_ids:
                    self._timeline.append(
                        DtcTimelineEntry(dtc_id, "disappeared", 0))
            self._current_dtcs = {r.dtc_id: r for r in records}

        elif sub_func == 0x01:
            # DTC数量
            self._dtc_count = response[3] if len(response) > 3 else 0

        elif sub_func == 0x04:
            # 快照数据
            if records:
                records[0].snapshot_data = response[3:]

        elif sub_func == 0x06:
            # 扩展数据
            if records:
                records[0].extended_data = response[3:]

        self._logger.info(f"解析到 {len(records)} 个DTC记录")
        return records

    def _update_timeline(self, dtc_id: int, new_status: int):
        """更新DTC时间线"""
        if dtc_id in self._current_dtcs:
            old_status = self._current_dtcs[dtc_id].status
            if old_status != new_status:
                self._timeline.append(
                    DtcTimelineEntry(dtc_id, "status_changed", new_status))
        else:
            self._timeline.append(
                DtcTimelineEntry(dtc_id, "appeared", new_status))

    def get_dtc_summary(self) -> dict:
        """获取DTC汇总信息"""
        confirmed = sum(1 for r in self._current_dtcs.values() if r.is_confirmed)
        pending = sum(1 for r in self._current_dtcs.values() if r.is_pending)
        history = sum(1 for r in self._current_dtcs.values() if r.is_history)

        return {
            "total": len(self._current_dtcs),
            "confirmed": confirmed,
            "pending": pending,
            "history": history,
        }

    def export_to_csv(self, file_path: str):
        """导出DTC列表为CSV文件"""
        try:
            import csv
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["DTC ID", "DTC名称", "状态", "状态描述",
                                 "严重度", "出现次数"])
                for record in self._current_dtcs.values():
                    writer.writerow([
                        record.dtc_id_hex,
                        record.definition,
                        f"0x{record.status:02X}",
                        record.status_description,
                        record.severity_level,
                        record.occurrence,
                    ])
            self._logger.info(f"DTC导出到 {file_path}")
        except Exception as e:
            self._logger.error(f"DTC导出失败: {e}")

    def clear(self):
        """清除当前DTC列表"""
        self._current_dtcs.clear()
        self._dtc_count = 0
