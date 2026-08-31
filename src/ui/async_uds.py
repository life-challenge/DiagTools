"""后台UDS调用工具

提供在QThread中执行阻塞式UDS调用的能力，避免阻塞GUI事件循环。

重要: 使用方必须保持对 UdsWorker 对象的引用（如存为实例属性），
否则worker会被Python GC回收，导致finished/error信号永远不被投递。
"""

from PyQt6.QtCore import QObject, pyqtSignal


class UdsWorker(QObject):
    """后台UDS调用工作线程对象

    用法:
        worker = UdsWorker(client.send_raw, data)
        thread = QThread(parent)
        worker.moveToThread(thread)
        worker.finished.connect(on_success)   # 回到GUI线程
        worker.error.connect(on_error)
        thread.started.connect(worker.run)
        self._worker = worker   # 关键: 保持引用防止GC回收
        thread.start()
    """

    finished = pyqtSignal(object)  # 调用结果（响应bytes或None）
    error = pyqtSignal(str)        # 异常信息

    def __init__(self, func, *args):
        super().__init__()
        self._func = func
        self._args = args

    def run(self):
        try:
            result = self._func(*self._args)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))
