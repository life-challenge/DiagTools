"""32位DLL桥接管理器

当DLL位数与当前Python位数不匹配（典型场景：32位OEM安全算法DLL + 64位Python）时，
本管理器在本机查找32位Python，启动桥接工作子进程（bridge_worker.py），
在其中加载DLL并通过stdin/stdout逐行JSON协议计算密钥。

32位Python探测顺序:
  1. 用户在安全面板指定的路径（持久化于配置项 security.python32_path）
  2. py启动器（py -3.x-32）
  3. Windows注册表 PythonCore x.x-32 的 InstallPath
"""

import json
import os
import queue
import subprocess
import sys
import threading
from src.utils.config_manager import get_config_manager
from src.log.log_manager import get_log_manager
from src.utils.paths import get_bridge_worker_path

_PY32_CONFIG_KEY = "security.python32_path"
_CALL_TIMEOUT = 30.0  # 单次请求超时（秒）


class BridgeError(RuntimeError):
    """桥接过程错误（可展示给用户的明确原因）"""


class BridgeProcessDied(BridgeError):
    """桥接子进程意外退出（常见于32位DLL使用__stdcall调用约定导致栈崩溃）"""

    def __init__(self, exit_code):
        super().__init__(f"桥接进程意外退出 (退出码: {exit_code})")
        self.exit_code = exit_code


class BridgeManager:
    """32位桥接子进程管理器

    用法:
        bm = BridgeManager()
        handle, fn_name, level = bm.load("xxx.dll")
        key = bm.generate_key(handle, seed)
        bm.shutdown()
    """

    def __init__(self, python_path: str = ""):
        self._python32 = python_path or ""
        self._proc = None
        self._responses: "queue.Queue" = queue.Queue()
        self._lock = threading.Lock()
        self._req_id = 0
        # handle -> (dll_path, stdcall, no_options)，用于子进程重启后重新加载（懒重加）；
        # no_options=该DLL的GenerateKeyEx无options参数（首次回退成功后记住）
        self._loaded: dict = {}
        self._handle_remap: dict = {}
        self._logger = get_log_manager().get_app_logger()

    # ---------------- 32位Python探测 ----------------

    @property
    def python32(self) -> str:
        """当前可用的32位Python可执行文件路径（空表示未找到）"""
        if not self._python32:
            self._python32 = self._detect_python32() or ""
            if self._python32:
                self._logger.info(f"探测到32位Python: {self._python32}")
        return self._python32

    def set_python32_path(self, path: str) -> bool:
        """用户手动指定32位Python路径。有效则持久化并重启桥接进程"""
        path = (path or "").strip()
        if not path or not os.path.isfile(path):
            return False
        if self._bits_of(path) != 32:
            return False
        self._python32 = path
        self.shutdown()  # 路径变化后旧进程作废，句柄映射一并清空（下次调用时懒重加）
        cfg = get_config_manager()
        cfg.set(_PY32_CONFIG_KEY, path)
        cfg.save_config()
        return True

    def _detect_python32(self) -> str:
        """按优先级探测本机32位Python"""
        # 1. 用户配置
        cfg = get_config_manager()
        path = (cfg.get(_PY32_CONFIG_KEY) or "").strip()
        if path and os.path.isfile(path) and self._bits_of(path) == 32:
            return path

        # 2. py启动器
        exe = self._detect_via_launcher()
        if exe:
            return exe

        # 3. 注册表
        return self._detect_via_registry() or ""

    def _detect_via_launcher(self) -> str:
        """通过py启动器查找32位Python（-3.x-32参数）"""
        if sys.platform != "win32":
            return ""
        for minor in range(14, 7, -1):
            try:
                r = subprocess.run(
                    ["py", f"-3.{minor}-32", "-c", "import sys;sys.stdout.write(sys.executable)"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                return ""  # py启动器不存在，后续都不必再试
            exe = (r.stdout or "").strip()
            if exe and os.path.isfile(exe) and self._bits_of(exe) == 32:
                return exe
        return ""

    def _detect_via_registry(self) -> str:
        """扫描注册表中安装的32位Python（PythonCore x.x-32）"""
        if sys.platform != "win32":
            return ""
        try:
            import winreg
        except ImportError:
            return ""

        candidates = []
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_READ, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
                try:
                    core = winreg.OpenKey(root, r"SOFTWARE\Python\PythonCore", 0, view)
                except OSError:
                    continue
                try:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(core, i)
                            i += 1
                        except OSError:
                            break
                        if not sub.endswith("-32"):
                            continue
                        try:
                            with winreg.OpenKey(core, sub + r"\InstallPath") as ik:
                                base, _ = winreg.QueryValueEx(ik, None)
                            exe = os.path.join(base or "", "python.exe")
                            if os.path.isfile(exe):
                                candidates.append(exe)
                        except OSError:
                            continue
                finally:
                    winreg.CloseKey(core)

        # 版本号倒序优先（字符串近似排序，如 3.12-32 > 3.9-32 不严格，但够用）
        for exe in sorted(set(candidates), reverse=True):
            if self._bits_of(exe) == 32:
                return exe
        return ""

    @staticmethod
    def _bits_of(exe: str) -> int:
        """检测某个python可执行文件的位数，失败返回0"""
        try:
            r = subprocess.run(
                [exe, "-c", "import struct;print(struct.calcsize('P')*8)"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return int(r.stdout.strip())
        except Exception:
            return 0

    # ---------------- 子进程管理 ----------------

    def _ensure_worker(self):
        """确保桥接子进程已启动"""
        if self._proc is not None and self._proc.poll() is None:
            return
        exe = self.python32
        if not exe:
            raise BridgeError(
                "DLL位数与当前Python不匹配，且未找到可用的32位Python，无法桥接加载。\n"
                "请安装32位Python（从 python.org 下载 Windows x86 安装包），\n"
                "然后在安全面板的\"32位Python\"栏指定其 python.exe 路径；\n"
                "或向算法提供方索取64位版本的DLL。")

        script = get_bridge_worker_path()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(
                [exe, script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=flags,
                encoding="utf-8", errors="replace", text=True)
        except OSError as e:
            raise BridgeError(f"启动桥接进程失败: {e}")

        # 清空旧响应队列，启动读取线程
        while not self._responses.empty():
            try:
                self._responses.get_nowait()
            except queue.Empty:
                break
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._logger.info(f"桥接进程已启动 (PID={self._proc.pid}, python={exe})")

    def _read_loop(self):
        """后台线程: 逐行读取子进程stdout放入队列，EOF时放入None哨兵"""
        proc = self._proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self._responses.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._responses.put(None)

    def _kill(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None

    def _call(self, req: dict, timeout: float = _CALL_TIMEOUT) -> dict:
        """发送请求并等待匹配的响应"""
        with self._lock:
            self._ensure_worker()
            self._req_id += 1
            req["id"] = self._req_id
            data = json.dumps(req, ensure_ascii=False) + "\n"
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (OSError, ValueError):
                self._kill()
                raise BridgeProcessDied(self._proc.poll() if self._proc else -1)

            while True:
                try:
                    line = self._responses.get(timeout=timeout)
                except queue.Empty:
                    self._kill()
                    raise BridgeError(f"桥接进程响应超时 ({timeout:.0f}秒)")
                if line is None:  # EOF: 子进程退出
                    code = self._proc.poll() if self._proc else -1
                    self._proc = None
                    raise BridgeProcessDied(code)
                try:
                    resp = json.loads(line)
                except (ValueError, TypeError):
                    continue  # 忽略非JSON输出（如DLL内部打印）
                if resp.get("id") != self._req_id:
                    continue
                if not resp.get("ok"):
                    raise BridgeError(resp.get("error", "未知错误"))
                return resp

    # ---------------- 对外接口 ----------------

    def load(self, dll_path: str, stdcall: bool = False,
             no_options: bool = False) -> tuple:
        """在桥接进程中加载DLL

        Args:
            dll_path: DLL路径
            stdcall: 以WinDLL(__stdcall)方式加载（崩溃恢复用）
            no_options: 已知该DLL的GenerateKeyEx无options参数，直接用7参签名

        Returns:
            (handle, fn_name, level)

        Raises:
            BridgeError: 未找到32位Python / DLL缺少导出函数等
        """
        resp = self._call({"cmd": "load", "dll_path": os.path.abspath(dll_path),
                           "stdcall": stdcall, "no_options": no_options})
        handle = resp["handle"]
        self._loaded[handle] = (os.path.abspath(dll_path), stdcall, no_options)
        return handle, resp["fn_name"], resp["level"]

    def generate_key(self, handle: int, seed: bytes,
                     level: int = None) -> bytes:
        """在桥接进程中计算密钥（懒重加）

        level为实际请求的安全等级，透传给DLL的GenerateKeyEx入参
        （None时用加载时探测的声明等级）。
        子进程重启（指定新32位Python路径）或进程崩溃后，旧句柄在新进程中无效，
        自动按记录的路径重新加载该DLL后重试一次。
        """
        try:
            return self._call_generate_key(handle, seed, level)
        except BridgeError as e:
            if isinstance(e, BridgeProcessDied) or "无效的DLL句柄" in str(e):
                return self._reload_and_retry(handle, seed, level, str(e))
            raise

    def _call_generate_key(self, handle: int, seed: bytes,
                           level: int = None) -> bytes:
        # handle=调用方持有的原始句柄；记录/回退都挂在该句柄上，保证重启后不丢失
        real_handle = self._handle_remap.get(handle, handle)
        req = {"cmd": "generate_key", "handle": real_handle,
               "seed_hex": seed.hex()}
        if level is not None:
            req["level"] = level
        resp = self._call(req)
        # 桥接进程内部签名回退成功（如DLL无options参数）: 记住，重启后直接命中7参签名
        if resp.get("fallback"):
            rec = self._loaded.get(handle)
            if rec:
                path, stdcall, _ = rec
                self._loaded[handle] = (path, stdcall, True)
            self._logger.info("桥接DLL签名回退成功(无options的7参版本)，已记住该签名")
        return bytes.fromhex(resp["key_hex"])

    def _reload_and_retry(self, old_handle: int, seed: bytes,
                           level: int, reason: str) -> bytes:
        """句柄失效/进程重启后，重新加载该DLL并重试一次"""
        rec = self._loaded.get(old_handle)
        if rec is None:
            raise BridgeError(f"桥接DLL句柄已失效且未记录来源: {reason}")
        path, stdcall, no_options = rec
        self._logger.info(f"桥接句柄失效({reason})，重新加载 {os.path.basename(path)} 后重试")
        new_handle, _, _ = self.load(path, stdcall=stdcall, no_options=no_options)
        # 新句柄同步一份记录，后续无论用哪个句柄查都能命中（包含回退更新）
        self._loaded[old_handle] = rec
        self._handle_remap[old_handle] = new_handle
        return self._call_generate_key(old_handle, seed, level)

    def shutdown(self):
        """关闭桥接子进程（程序退出/路径切换时调用）

        注意: 保留_loaded注册表，进程重启后旧句柄可按记录懒重加。
        """
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._call({"cmd": "shutdown"}, timeout=3)
            except BridgeError:
                pass
            try:
                if self._proc is not None:
                    self._proc.wait(timeout=2)
            except Exception:
                self._kill()
        self._proc = None
        self._handle_remap.clear()
