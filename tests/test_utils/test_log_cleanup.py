# -*- coding: utf-8 -*-
"""日志自动清理测试（cleanup_old_logs 此前为死代码，启动时开始生效）"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.log.log_manager import LogManager


class TestCleanupOldLogs(unittest.TestCase):

    def setUp(self):
        LogManager.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mgr = LogManager(log_base_dir=self.tmp.name)
        self.addCleanup(LogManager.reset)

    def _make_file(self, subdir, name, age_days=None):
        path = os.path.join(self.tmp.name, subdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("log line\n")
        if age_days is not None:
            ts = time.time() - age_days * 86400
            os.utime(path, (ts, ts))
        return path

    def test_deletes_only_expired_files(self):
        """超过保留天数的.log删除，新文件与未过期文件保留"""
        expired = self._make_file("app", "app_old.log", age_days=40)
        fresh = self._make_file("app", "app_new.log")
        recent = self._make_file("diag", "diag_recent.log", age_days=5)

        deleted = self.mgr.cleanup_old_logs(30)

        self.assertEqual(deleted, 1)
        self.assertFalse(os.path.exists(expired))
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(os.path.exists(recent))

    def test_non_log_files_untouched(self):
        """非.log后缀文件（如索引/说明）不清理"""
        keep = self._make_file("app", "README.txt", age_days=400)
        deleted = self.mgr.cleanup_old_logs(30)
        self.assertEqual(deleted, 0)
        self.assertTrue(os.path.exists(keep))

    def test_all_subdirs_covered(self):
        """五个日志子目录均纳入清理范围"""
        expired_paths = [
            self._make_file(sub, f"{sub}_old.log", age_days=100)
            for sub in ("diag", "flash", "comm", "sequence", "app")
        ]
        self.assertEqual(self.mgr.cleanup_old_logs(30), 5)
        self.assertTrue(all(not os.path.exists(p) for p in expired_paths))

    def test_custom_retention_days(self):
        """保留天数可配置: 7天阈值下5天前的文件保留、10天前的删除"""
        keep = self._make_file("app", "app_5d.log", age_days=5)
        gone = self._make_file("app", "app_10d.log", age_days=10)
        self.assertEqual(self.mgr.cleanup_old_logs(7), 1)
        self.assertTrue(os.path.exists(keep))
        self.assertFalse(os.path.exists(gone))


if __name__ == "__main__":
    unittest.main()
