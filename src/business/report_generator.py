"""诊断报告生成模块

支持生成DTC报告、DID快照报告、全车扫描报告、刷写报告。
格式: HTML（带样式）和纯文本。
"""

import time
import os
from typing import Optional
from src.log.log_manager import get_log_manager


class ReportGenerator:
    """诊断报告生成器"""

    def __init__(self, output_dir: str = "reports"):
        self._output_dir = output_dir
        self._logger = get_log_manager().get_app_logger()
        os.makedirs(output_dir, exist_ok=True)

    def generate_dtc_report(self, dtc_records: list, ecu_name: str = "",
                            format: str = "html") -> str:
        """生成DTC诊断报告"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"dtc_report_{timestamp}"

        if format == "html":
            filepath = os.path.join(self._output_dir, f"{filename}.html")
            self._generate_dtc_html(dtc_records, ecu_name, filepath)
        else:
            filepath = os.path.join(self._output_dir, f"{filename}.txt")
            self._generate_dtc_text(dtc_records, ecu_name, filepath)

        self._logger.info(f"DTC报告已生成: {filepath}")
        return filepath

    def generate_did_report(self, did_values: dict, ecu_name: str = "",
                            format: str = "html") -> str:
        """生成DID快照报告"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self._output_dir, f"did_report_{timestamp}.html")

        self._generate_did_html(did_values, ecu_name, filepath)
        self._logger.info(f"DID报告已生成: {filepath}")
        return filepath

    def generate_flash_report(self, ecu_name: str, file_path: str,
                              steps: list, result: str,
                              error_msg: str = "",
                              start_time: str = "",
                              end_time: str = "") -> str:
        """生成刷写报告（刷写终态时自动调用）

        Args:
            steps: 步骤文本列表（含状态标记，如"✔ 5. 数据传输"）
            result: 刷写完成/刷写失败/已取消
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self._output_dir, f"flash_report_{timestamp}.html")

        ok = result == "刷写完成"
        result_color = "#4CAF50" if ok else "#F44336"
        step_rows = "".join(
            f"<tr><td>{s}</td></tr>" for s in steps)

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>刷写报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
.header {{ background: #1a237e; color: white; padding: 20px; border-radius: 8px; }}
.summary {{ display: flex; gap: 20px; margin: 20px 0; }}
.summary-card {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); flex: 1; text-align: center; }}
.summary-card h3 {{ margin: 0; font-size: 1.6em; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
th {{ background: #37474F; color: white; padding: 12px; text-align: left; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #eee; font-family: Consolas, monospace; }}
</style></head><body>
<div class="header">
<h1>刷写报告</h1>
<p>时间: {time.strftime("%Y-%m-%d %H:%M:%S")}</p>
<p>ECU: {ecu_name}</p>
</div>
<div class="summary">
<div class="summary-card"><p>结果</p><h3 style="color: {result_color};">{result}</h3></div>
<div class="summary-card"><p>步骤数</p><h3>{len(steps)}</h3></div>
<div class="summary-card"><p>开始</p><h3>{start_time or '--'}</h3></div>
<div class="summary-card"><p>结束</p><h3>{end_time or '--'}</h3></div>
</div>
<p><b>刷写文件:</b> {file_path}</p>
{"<p style='color:#F44336;'><b>错误:</b> " + error_msg + "</p>" if error_msg else ""}
<table>
<tr><th>执行步骤（含状态标记）</th></tr>
{step_rows}
</table></body></html>"""

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
        self._logger.info(f"刷写报告已生成: {filepath}")
        return filepath

    def _generate_dtc_html(self, records, ecu_name, filepath):
        """生成DTC HTML报告"""
        confirmed = sum(1 for r in records if r.is_confirmed)
        pending = sum(1 for r in records if r.is_pending)

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>DTC诊断报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
.header {{ background: #1a237e; color: white; padding: 20px; border-radius: 8px; }}
.summary {{ display: flex; gap: 20px; margin: 20px 0; }}
.summary-card {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); flex: 1; text-align: center; }}
.summary-card h3 {{ margin: 0; font-size: 2em; }}
.confirmed {{ color: #F44336; }}
.pending {{ color: #FF9800; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
th {{ background: #37474F; color: white; padding: 12px; text-align: left; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #eee; }}
tr:hover {{ background: #f5f5f5; }}
.severity-confirmed {{ color: #F44336; font-weight: bold; }}
.severity-pending {{ color: #FF9800; font-weight: bold; }}
.severity-history {{ color: #9E9E9E; }}
</style></head><body>
<div class="header">
<h1>DTC 诊断报告</h1>
<p>时间: {time.strftime("%Y-%m-%d %H:%M:%S")}</p>
<p>ECU: {ecu_name}</p>
</div>
<div class="summary">
<div class="summary-card"><p>总计</p><h3>{len(records)}</h3></div>
<div class="summary-card"><p>已确认</p><h3 class="confirmed">{confirmed}</h3></div>
<div class="summary-card"><p>待确认</p><h3 class="pending">{pending}</h3></div>
</div>
<table>
<tr><th>DTC ID</th><th>名称</th><th>状态</th><th>状态描述</th><th>严重度</th><th>出现次数</th></tr>
"""
        for r in records:
            sev_class = f"severity-{r.severity_level}"
            html += f"""<tr>
<td>{r.dtc_id_hex}</td><td>{r.definition}</td>
<td>0x{r.status:02X}</td><td>{r.status_description}</td>
<td class="{sev_class}">{r.severity_level}</td><td>{r.occurrence}</td>
</tr>"""

        html += "</table></body></html>"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)

    def _generate_dtc_text(self, records, ecu_name, filepath):
        """生成DTC文本报告"""
        lines = [
            f"DTC 诊断报告",
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"ECU: {ecu_name}",
            f"总计: {len(records)} 个DTC",
            "=" * 60,
        ]
        for r in records:
            lines.append(
                f"{r.dtc_id_hex} | {r.definition:20s} | "
                f"状态:0x{r.status:02X} | {r.severity_level}")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

    def _generate_did_html(self, did_values, ecu_name, filepath):
        """生成DID HTML报告"""
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>DID快照报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', Arial, sans-serif; margin: 20px; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ background: #37474F; color: white; padding: 10px; text-align: left; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #eee; }}
</style></head><body>
<h1>DID 快照报告</h1>
<p>时间: {time.strftime("%Y-%m-%d %H:%M:%S")} | ECU: {ecu_name}</p>
<table>
<tr><th>DID ID</th><th>名称</th><th>当前值</th><th>原始数据</th><th>单位</th></tr>
"""
        for did_id, val in did_values.items():
            name = val.definition.name if val.definition else f"0x{did_id:04X}"
            unit = val.definition.unit if val.definition else ""
            html += f"""<tr><td>0x{did_id:04X}</td><td>{name}</td>
<td>{val.display_value}</td><td>{val.raw_data.hex(' ')}</td><td>{unit}</td></tr>"""

        html += "</table></body></html>"
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
