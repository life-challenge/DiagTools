"""PyQt6 界面层：主窗口 + 视图(一级导航)/面板/控件/对话框 + 主题样式。

约定: 连接建立后由主窗口向各视图注入 UDS 客户端(set_uds_client)与
CAN 接口(set_can_interface)，断开时置 None。
"""
