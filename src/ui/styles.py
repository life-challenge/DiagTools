"""QSS主题样式定义"""


# 颜色常量
COLORS = {
    "positive": "#4CAF50",     # 正响应=绿色
    "negative": "#F44336",     # 负响应/错误=红色
    "warning": "#FF9800",      # 警告=橙色
    "info": "#2196F3",         # 信息=蓝色
    "tx": "#64B5F6",           # TX请求=蓝色
    "rx_positive": "#81C784",  # RX正响应=绿色
    "rx_negative": "#E57373",  # RX负响应=红色
}


DARK_THEME = """
/* 全局样式 */
QMainWindow {
    background-color: #1E1E2E;
    color: #CDD6F4;
}

QWidget {
    background-color: #1E1E2E;
    color: #CDD6F4;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
}

/* 菜单栏 */
QMenuBar {
    background-color: #181825;
    color: #CDD6F4;
    border-bottom: 1px solid #313244;
    padding: 2px;
}

QMenuBar::item:selected {
    background-color: #45475A;
    border-radius: 4px;
}

QMenu {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #313244;
    border-radius: 4px;
    padding: 4px;
}

QMenu::item:selected {
    background-color: #45475A;
    border-radius: 4px;
}

QMenu::separator {
    height: 1px;
    background-color: #313244;
    margin: 4px 8px;
}

/* 工具栏 */
QToolBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    padding: 4px;
    spacing: 4px;
}

QToolButton {
    background-color: transparent;
    color: #CDD6F4;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}

QToolButton:hover {
    background-color: #313244;
    border-color: #45475A;
}

QToolButton:pressed {
    background-color: #45475A;
}

/* 状态栏 */
QStatusBar {
    background-color: #181825;
    color: #A6ADC8;
    border-top: 1px solid #313244;
    font-size: 11px;
}

QStatusBar::item {
    border: none;
    padding: 1px 2px;
}

/* 状态栏独立状态块（§8: 拆分状态块，清晰分隔） */
QLabel#sb_block {
    background-color: #1E1E2E;
    border: 1px solid #313244;
    border-radius: 3px;
    padding: 2px 8px;
}

/* 标签页 */
QTabWidget::pane {
    border: 1px solid #313244;
    background-color: #1E1E2E;
    border-radius: 4px;
}

QTabBar::tab {
    background-color: #181825;
    color: #A6ADC8;
    border: 1px solid #313244;
    padding: 6px 16px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

QTabBar::tab:selected {
    background-color: #313244;
    color: #CDD6F4;
    border-bottom-color: #313244;
    border-top: 2px solid #89B4FA;
}

QTabBar::tab:hover {
    background-color: #45475A;
    color: #CDD6F4;
}

/* 按钮 */
QPushButton {
    background-color: #313244;
    color: #CDD6F4;
    border: 1px solid #45475A;
    border-radius: 4px;
    padding: 6px 16px;
    font-size: 12px;
    min-width: 60px;
}

QPushButton:hover {
    background-color: #45475A;
    border-color: #585B70;
}

QPushButton:pressed {
    background-color: #585B70;
}

QPushButton:disabled {
    background-color: #181825;
    color: #585B70;
    border-color: #313244;
}

/* 工具栏启动/停止对: 连接=亮黄色▶可点，点击(运行中)后置灰不可再点；
   断开=红色■，未连接时灰色禁用，连接后可点（与连接钮互补） */
QPushButton#btn_connect {
    background-color: #FFC107;
    border: 1px solid #FFD54F;
    color: #1A1A1A;
    font-weight: bold;
}

QPushButton#btn_connect:hover {
    background-color: #FFD54F;
}

/* 已连接时置灰（ID选择器特异性高于通用:disabled，须显式覆盖） */
QPushButton#btn_connect:disabled {
    background-color: #181825;
    color: #585B70;
    border-color: #313244;
}

QPushButton#btn_disconnect {
    background-color: #C62828;
    border-color: #F44336;
    color: #FFFFFF;
    font-weight: bold;
}

QPushButton#btn_disconnect:hover {
    background-color: #D32F2F;
}

/* 未连接时置灰不可点（ID选择器特异性高于通用:disabled，须显式覆盖，
   否则禁用态仍渲染为红色、看似可点击） */
QPushButton#btn_disconnect:disabled {
    background-color: #181825;
    color: #585B70;
    border-color: #313244;
}

/* 按钮层级（§9: Primary/Secondary/Danger） */
QPushButton#btn_primary {
    background-color: #2B579A;
    border-color: #89B4FA;
    color: #EFF6FF;
    font-weight: bold;
}

QPushButton#btn_primary:hover {
    background-color: #3367B5;
}

QPushButton#btn_primary:disabled {
    background-color: #181825;
    color: #585B70;
    border-color: #313244;
}

QPushButton#btn_danger {
    background-color: #7F1D1D;
    border-color: #F44336;
}

QPushButton#btn_danger:hover {
    background-color: #991B1B;
}

/* 输入框 */
QLineEdit {
    background-color: #313244;
    color: #CDD6F4;
    border: 1px solid #45475A;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #585B70;
}

QLineEdit:focus {
    border-color: #89B4FA;
}

/* 下拉框 */
QComboBox {
    background-color: #313244;
    color: #CDD6F4;
    border: 1px solid #45475A;
    border-radius: 4px;
    padding: 4px 8px;
}

QComboBox:hover {
    border-color: #585B70;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #313244;
    color: #CDD6F4;
    selection-background-color: #45475A;
    border: 1px solid #45475A;
}

/* 表格 */
QTableWidget {
    background-color: #1E1E2E;
    alternate-background-color: #181825;
    color: #CDD6F4;
    gridline-color: #313244;
    border: 1px solid #313244;
    border-radius: 4px;
    selection-background-color: #45475A;
}

QTableWidget::item {
    padding: 4px;
}

QTableWidget::item:selected {
    background-color: #45475A;
}

QHeaderView::section {
    background-color: #181825;
    color: #A6ADC8;
    border: 1px solid #313244;
    padding: 4px 8px;
    font-weight: bold;
}

/* 树形控件 */
QTreeWidget {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #313244;
    border-radius: 4px;
    alternate-background-color: #181825;
}

QTreeWidget::item:selected {
    background-color: #45475A;
}

QTreeWidget::item:hover {
    background-color: #313244;
}

QTreeWidget::item {
    padding: 3px 4px;
    border: none;
}

/* 文本区域 */
QTextEdit, QPlainTextEdit {
    background-color: #11111B;
    color: #CDD6F4;
    border: 1px solid #313244;
    border-radius: 4px;
    font-family: "Consolas", monospace;
    font-size: 12px;
    selection-background-color: #45475A;
}

/* 分组框 */
QGroupBox {
    border: 1px solid #313244;
    border-radius: 6px;
    margin-top: 8px;
    padding: 14px 10px 10px 10px;
    font-weight: bold;
    color: #A6ADC8;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0px 4px;
    color: #89B4FA;
}

/* 滚动条 */
QScrollBar:vertical {
    background-color: #1E1E2E;
    width: 10px;
    border: none;
}

QScrollBar::handle:vertical {
    background-color: #45475A;
    border-radius: 5px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background-color: #585B70;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background-color: #1E1E2E;
    height: 10px;
    border: none;
}

QScrollBar::handle:horizontal {
    background-color: #45475A;
    border-radius: 5px;
    min-width: 20px;
}

/* 复选框 */
QCheckBox {
    color: #CDD6F4;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #45475A;
    border-radius: 3px;
    background-color: #313244;
}

QCheckBox::indicator:checked {
    background-color: #89B4FA;
    border-color: #89B4FA;
}

/* 进度条 */
QProgressBar {
    background-color: #313244;
    border: 1px solid #45475A;
    border-radius: 4px;
    text-align: center;
    color: #CDD6F4;
    height: 20px;
}

QProgressBar::chunk {
    background-color: #89B4FA;
    border-radius: 3px;
}

/* 分割器 */
QSplitter::handle {
    background-color: #313244;
}

QSplitter::handle:horizontal {
    width: 2px;
}

QSplitter::handle:vertical {
    height: 2px;
}

/* 工具提示 */
QToolTip {
    background-color: #313244;
    color: #CDD6F4;
    border: 1px solid #45475A;
    border-radius: 4px;
    padding: 4px;
}

/* 标签 */
QLabel#label_status_connected {
    color: #4CAF50;
    font-weight: bold;
}

QLabel#label_status_disconnected {
    color: #F44336;
    font-weight: bold;
}

QLabel#label_title {
    font-size: 14px;
    font-weight: bold;
    color: #89B4FA;
}

/* ECU在线状态徽章 */
QLabel#online_badge {
    color: #4ADE80;
    background-color: #123524;
    border: 1px solid #2E7D32;
    border-radius: 9px;
    padding: 2px 10px;
    font-weight: bold;
}

QLabel#offline_badge {
    color: #9CA3AF;
    background-color: #262637;
    border: 1px solid #45475A;
    border-radius: 9px;
    padding: 2px 10px;
    font-weight: bold;
}

/* 日志区头部条: 与表格内容之间提供视觉分隔 */
QWidget#log_toolbar, QWidget#dock_header {
    background-color: #181825;
    border-bottom: 1px solid #313244;
}

/* 快捷入口条（图标+名称，§6 功能页签的快捷入口形式） */
QWidget#quick_bar {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
}

QPushButton#quick_btn {
    background-color: transparent;
    color: #A6ADC8;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 5px 10px;
    min-width: 0px;
}

QPushButton#quick_btn:hover {
    background-color: #313244;
    color: #CDD6F4;
}

QPushButton#quick_btn_sel {
    background-color: #2B579A;
    color: #EFF6FF;
    border: 1px solid #89B4FA;
    border-radius: 4px;
    padding: 5px 10px;
    min-width: 0px;
    font-weight: bold;
}

/* 信息卡顶部状态区小卡 */
QWidget#status_card {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
}
"""


LIGHT_THEME = """
QMainWindow {
    background-color: #F5F5F5;
    color: #333333;
}

QWidget {
    background-color: #F5F5F5;
    color: #333333;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
}

QMenuBar {
    background-color: #FFFFFF;
    color: #333333;
    border-bottom: 1px solid #E0E0E0;
}

QMenuBar::item:selected {
    background-color: #E3F2FD;
    border-radius: 4px;
}

QMenu {
    background-color: #FFFFFF;
    color: #333333;
    border: 1px solid #E0E0E0;
}

QMenu::item:selected {
    background-color: #E3F2FD;
}

QToolBar {
    background-color: #FFFFFF;
    border-bottom: 1px solid #E0E0E0;
}

QToolButton {
    background-color: transparent;
    color: #333333;
    border-radius: 4px;
    padding: 4px 8px;
}

QToolButton:hover {
    background-color: #E0E0E0;
}

QStatusBar {
    background-color: #FFFFFF;
    color: #666666;
    border-top: 1px solid #E0E0E0;
}

QTabBar::tab {
    background-color: #E0E0E0;
    color: #666666;
    border: 1px solid #BDBDBD;
    padding: 6px 16px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

QTabBar::tab:selected {
    background-color: #FFFFFF;
    color: #333333;
}

QPushButton {
    background-color: #E0E0E0;
    color: #333333;
    border: 1px solid #BDBDBD;
    border-radius: 4px;
    padding: 6px 16px;
}

QPushButton:hover {
    background-color: #BDBDBD;
}

QLineEdit, QComboBox {
    background-color: #FFFFFF;
    color: #333333;
    border: 1px solid #BDBDBD;
    border-radius: 4px;
    padding: 4px 8px;
}

QTableWidget {
    background-color: #FFFFFF;
    alternate-background-color: #F5F5F5;
    color: #333333;
    gridline-color: #E0E0E0;
    border: 1px solid #E0E0E0;
}

QTreeWidget {
    background-color: #FFFFFF;
    color: #333333;
    border: 1px solid #E0E0E0;
}

QTextEdit, QPlainTextEdit {
    background-color: #FFFFFF;
    color: #333333;
    border: 1px solid #E0E0E0;
}

QHeaderView::section {
    background-color: #F5F5F5;
    color: #333333;
    border: 1px solid #E0E0E0;
    padding: 4px 8px;
}

QStatusBar::item {
    border: none;
    padding: 1px 2px;
}

QLabel#sb_block {
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-radius: 3px;
    padding: 2px 8px;
}

QPushButton#btn_primary {
    background-color: #1976D2;
    border-color: #2196F3;
    color: #FFFFFF;
    font-weight: bold;
}

QPushButton#btn_danger {
    background-color: #C62828;
    border-color: #F44336;
    color: #FFFFFF;
}

/* 工具栏启动/停止对: 连接亮黄/断开红色 */
QPushButton#btn_connect {
    background-color: #FFC107;
    border: 1px solid #FFD54F;
    color: #1A1A1A;
    font-weight: bold;
}

QPushButton#btn_connect:hover {
    background-color: #FFD54F;
}

/* 已连接时置灰（ID选择器特异性高于通用:disabled，须显式覆盖） */
QPushButton#btn_connect:disabled {
    background-color: #E0E0E0;
    color: #9E9E9E;
    border-color: #BDBDBD;
}

QPushButton#btn_disconnect {
    background-color: #C62828;
    border-color: #F44336;
    color: #FFFFFF;
    font-weight: bold;
}

QPushButton#btn_disconnect:hover {
    background-color: #D32F2F;
}

/* 未连接时置灰不可点（ID选择器特异性高于通用:disabled，须显式覆盖，
   否则禁用态仍渲染为红色、看似可点击） */
QPushButton#btn_disconnect:disabled {
    background-color: #E0E0E0;
    color: #9E9E9E;
    border-color: #BDBDBD;
}

QLabel#online_badge {
    color: #2E7D32;
    background-color: #E8F5E9;
    border: 1px solid #4CAF50;
    border-radius: 9px;
    padding: 2px 10px;
    font-weight: bold;
}

QLabel#offline_badge {
    color: #757575;
    background-color: #EEEEEE;
    border: 1px solid #BDBDBD;
    border-radius: 9px;
    padding: 2px 10px;
    font-weight: bold;
}

QWidget#log_toolbar, QWidget#dock_header {
    background-color: #EEEEEE;
    border-bottom: 1px solid #E0E0E0;
}

QPushButton#quick_btn {
    background-color: transparent;
    color: #666666;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 5px 10px;
    min-width: 0px;
}

QPushButton#quick_btn:hover {
    background-color: #E0E0E0;
    color: #333333;
}

QPushButton#quick_btn_sel {
    background-color: #1976D2;
    color: #FFFFFF;
    border: 1px solid #2196F3;
    border-radius: 4px;
    padding: 5px 10px;
    min-width: 0px;
    font-weight: bold;
}

QWidget#status_card {
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-radius: 6px;
}
"""


def get_theme(name: str = "dark") -> str:
    """获取主题样式表"""
    if name == "light":
        return LIGHT_THEME
    return DARK_THEME
