"""安全算法加载与管理模块

插件式架构，支持Python脚本或DLL动态加载安全算法。
功能：
- 动态加载/卸载算法插件
- 算法配置档案管理（ECU+安全等级+算法文件绑定）
- 离线测试（无需连接ECU即可验证算法）
- 一键解锁（自动种子请求+密钥计算+密钥发送）
- 多等级批量解锁
"""

import os
import sys
import struct
import importlib
import importlib.util
import ctypes
import json
import time
from typing import Optional
from src.log.log_manager import get_log_manager
from src.business.bridge_manager import BridgeManager, BridgeError
from src.utils.paths import get_plugins_dir

# PE Machine类型 -> 位数描述
_PE_MACHINE_BITS = {0x014C: 32, 0x8664: 64, 0xAA64: 64, 0x0200: 64}


class SecurityAlgorithmInfo:
    """算法插件信息"""

    def __init__(self, name: str, description: str, level: int,
                 version: str = "1.0", author: str = "",
                 file_path: str = "", is_dll: bool = False):
        self.name = name
        self.description = description
        self.level = level
        self.version = version
        self.author = author
        self.file_path = file_path
        self.is_dll = is_dll
        self.instance = None  # 算法实例（Python插件实例或_DllAlgorithm适配器）

    def __repr__(self):
        return f"SecurityAlgorithm(name={self.name}, level={self.level}, file={self.file_path})"


class _DllAlgorithm:
    """DLL算法适配器，封装为与Python插件一致的 generate_key(seed) -> key 接口

    DLL需导出C函数:
        int generate_key(unsigned char* seed, int seed_len,
                         unsigned char* key, int* key_len);
    返回值0表示成功，非0为错误码。
    可选导出: int get_security_level(); 用于声明算法对应的安全等级。
    """

    _KEY_BUF_SIZE = 64  # 密钥输出缓冲区上限（UDS密钥通常不超过16字节）

    def __init__(self, dll):
        self._fn = dll.generate_key
        self._fn.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_int),
        ]
        self._fn.restype = ctypes.c_int

    def generate_key(self, seed: bytes) -> bytes:
        seed_buf = (ctypes.c_ubyte * len(seed))(*seed)
        key_buf = (ctypes.c_ubyte * self._KEY_BUF_SIZE)()
        key_len = ctypes.c_int(self._KEY_BUF_SIZE)
        ret = self._fn(seed_buf, len(seed), key_buf, ctypes.byref(key_len))
        if ret != 0:
            raise RuntimeError(f"DLL generate_key 返回错误码: {ret}")
        if not 0 <= key_len.value <= self._KEY_BUF_SIZE:
            raise RuntimeError(f"DLL返回的密钥长度无效: {key_len.value}")
        return bytes(key_buf[:key_len.value])


class _GenerateKeyExAlgorithm:
    """OEM标准安全算法DLL接口适配器（返回0表示成功）

    支持三种签名，按顺序自动回退:
      8参标准: GenerateKeyEx(seed, size, level, variant, options,
                             key, max_size, actual_size)
      7参无options: GenerateKeyEx(seed, size, level, variant,
                                  key, max_size, actual_size)
          （部分供应商DLL如TP_DASH，传options会把key缓冲区位移，
            典型症状: access violation writing 0x40/0x0，已在实机验证）
      7参旧版: GenerateKey(seed, size, level, variant,
                           key, max_size, actual_size)
    """

    _KEY_BUF_SIZE = 64

    def __init__(self, dll, level: int, fn_name: str):
        self._level = level
        self._fn = getattr(dll, fn_name)
        self._fn.restype = ctypes.c_ulong
        # 回退候选签名: [是否带options]，8参失败自动改试无options的7参版本；
        # 显式选择旧版GenerateKey时直接用其7参签名不试回退链。
        if fn_name == "GenerateKey":
            self._variants = [False]
        else:
            self._variants = [True, False]
        self._variant_idx = 0
        self._apply_argtypes()

    def _apply_argtypes(self):
        with_options = self._variants[self._variant_idx]
        argtypes = [
            ctypes.c_char_p, ctypes.c_ulong,      # seed, seed_size
            ctypes.c_ulong, ctypes.c_char_p,      # security_level, variant
        ]
        if with_options:
            argtypes.append(ctypes.c_char_p)      # options
        argtypes += [
            ctypes.c_char_p, ctypes.c_ulong,      # key_buf, max_key_size
            ctypes.POINTER(ctypes.c_ulong),       # actual_key_size
        ]
        self._fn.argtypes = argtypes

    def _call_once(self, seed: bytes):
        key_buf = ctypes.create_string_buffer(self._KEY_BUF_SIZE)
        actual = ctypes.c_ulong(0)
        args = [bytes(seed), len(seed), self._level, b"default"]
        if self._variants[self._variant_idx]:
            args.append(b"")
        args += [key_buf, self._KEY_BUF_SIZE, ctypes.byref(actual)]
        ret = self._fn(*args)
        if ret != 0:
            raise RuntimeError(f"DLL密钥计算返回错误码: {ret}")
        if not 0 <= actual.value <= self._KEY_BUF_SIZE:
            raise RuntimeError(f"DLL返回的密钥长度无效: {actual.value}")
        return key_buf.raw[:actual.value]

    def generate_key(self, seed: bytes) -> bytes:
        try:
            return self._call_once(seed)
        except OSError:
            # access violation等: 常见于签名不匹配（如DLL无options参数），
            # 切换下一个候选签名重试；进程内调用崩溃风险由调用方承担，
            # 回退前已验证候选签名存在。
            if self._variant_idx + 1 >= len(self._variants):
                raise RuntimeError(
                    "DLL密钥计算崩溃（访问冲突），已尝试全部候选签名。\n"
                    "请确认该DLL的GenerateKeyEx函数原型。")
            self._variant_idx += 1
            self._apply_argtypes()
            return self._call_once(seed)


class _BridgedAlgorithm:
    """桥接算法适配器: 位数不匹配的DLL在32位桥接子进程中计算密钥"""

    def __init__(self, bridge: BridgeManager, handle: int):
        self._bridge = bridge
        self._handle = handle

    def generate_key(self, seed: bytes) -> bytes:
        return self._bridge.generate_key(self._handle, seed)


class SecurityManager:
    """安全算法管理器

    管理安全算法插件的加载、卸载和调用。
    """

    def __init__(self, plugins_dir: str = ""):
        # 默认锚定到项目根（源码运行=仓库根；打包后=exe旁边），
        # 避免双击启动时相对CWD找不到插件目录
        self._plugins_dir = plugins_dir or get_plugins_dir()
        self._algorithms: dict[int, SecurityAlgorithmInfo] = {}  # level -> info
        self._configs: dict = {}  # 配置档案
        self._logger = get_log_manager().get_app_logger()
        self._unlock_timers: dict[int, float] = {}  # level -> unlock_timestamp
        self._last_error = ""  # 最后一次加载失败的详细原因
        self._bridge: Optional[BridgeManager] = None  # 32位桥接管理器（按需创建）

    @property
    def loaded_algorithms(self) -> dict[int, SecurityAlgorithmInfo]:
        """已加载的算法列表"""
        return dict(self._algorithms)

    @property
    def last_error(self) -> str:
        """最后一次加载失败的详细原因"""
        return self._last_error

    @staticmethod
    def _pe_machine(file_path: str) -> Optional[int]:
        """读取PE文件的Machine字段（判断32/64位），无法解析返回None"""
        try:
            with open(file_path, "rb") as f:
                header = f.read(0x40)
                if len(header) < 0x40 or header[:2] != b"MZ":
                    return None
                pe_off = struct.unpack_from("<I", header, 0x3C)[0]
                f.seek(pe_off)
                sig = f.read(6)
                if len(sig) < 6 or sig[:4] != b"PE\x00\x00":
                    return None
                return struct.unpack("<H", sig[4:6])[0]
        except Exception:
            return None

    def load_algorithm_file(self, file_path: str) -> Optional[SecurityAlgorithmInfo]:
        """从Python文件或DLL加载安全算法插件
    
        Args:
            file_path: 算法文件路径（.py或.dll文件）
    
        Returns:
            加载成功返回算法信息，失败返回None（原因见last_error）
        """
        self._last_error = ""
        try:
            if not os.path.exists(file_path):
                self._last_error = f"算法文件不存在: {file_path}"
                self._logger.error(self._last_error)
                return None
    
            if file_path.endswith('.py'):
                return self._load_python_plugin(file_path)
            elif file_path.lower().endswith('.dll'):
                return self._load_dll_plugin(file_path)
            else:
                self._last_error = f"不支持的算法文件格式: {file_path}"
                self._logger.error(self._last_error)
                return None
    
        except Exception as e:
            self._last_error = f"加载算法文件异常: {e}"
            self._logger.error(f"加载算法文件失败 [{file_path}]: {e}")
            return None

    def _load_python_plugin(self, file_path: str) -> Optional[SecurityAlgorithmInfo]:
        """加载Python脚本插件"""
        module_name = os.path.splitext(os.path.basename(file_path))[0]

        # 确保插件目录在sys.path中
        plugin_dir = os.path.dirname(os.path.abspath(file_path))
        if plugin_dir not in sys.path:
            sys.path.insert(0, plugin_dir)

        # 先确保基类已加载
        base_path = os.path.join(plugin_dir, 'algorithm_base.py')
        if os.path.exists(base_path) and 'algorithm_base' not in sys.modules:
            base_spec = importlib.util.spec_from_file_location('algorithm_base', base_path)
            if base_spec and base_spec.loader:
                base_mod = importlib.util.module_from_spec(base_spec)
                sys.modules['algorithm_base'] = base_mod
                base_spec.loader.exec_module(base_mod)

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # 查找继承自基类的算法类
        try:
            from algorithm_base import SecurityAlgorithmBase
        except ImportError:
            try:
                from plugins.security_algorithms.algorithm_base import SecurityAlgorithmBase
            except ImportError:
                self._last_error = "无法导入SecurityAlgorithmBase基类"
                self._logger.error(self._last_error)
                return None

        algo_instance = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and
                    issubclass(attr, SecurityAlgorithmBase) and
                    attr is not SecurityAlgorithmBase):
                algo_instance = attr()
                break

        if algo_instance is None:
            self._last_error = f"文件中未找到继承SecurityAlgorithmBase的算法类: {file_path}"
            self._logger.warning(self._last_error)
            return None

        info = SecurityAlgorithmInfo(
            name=algo_instance.name,
            description=algo_instance.description,
            level=algo_instance.level,
            version=getattr(algo_instance, 'version', '1.0'),
            author=getattr(algo_instance, 'author', ''),
            file_path=file_path,
        )
        info.instance = algo_instance

        self._algorithms[info.level] = info
        self._logger.info(f"加载安全算法: {info.name} (Level={info.level}) from {file_path}")
        return info

    def _load_dll_plugin(self, file_path: str) -> Optional[SecurityAlgorithmInfo]:
        """加载DLL插件

        支持的导出函数（按优先级）:
          - GenerateKeyEx: OEM标准8参数接口（常见于供应商提供的安全算法DLL）
          - GenerateKey:   旧版7参数接口
          - generate_key:  简化接口 (seed, seed_len, key, key_len)
        """
        file_path = os.path.abspath(file_path)

        # ---- 位数检查: 位数不匹配时尝试通过桥接子进程加载 ----
        machine = self._pe_machine(file_path)
        py_bits = struct.calcsize("P") * 8
        if machine is not None:
            dll_bits = _PE_MACHINE_BITS.get(machine)
            if dll_bits and dll_bits != py_bits:
                return self._load_dll_via_bridge(file_path, dll_bits, py_bits)

        try:
            # 将DLL所在目录加入依赖搜索路径（支持DLL依赖同目录下的其他DLL）
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(os.path.dirname(file_path))

            dll = ctypes.CDLL(file_path)

            # ---- 探测导出函数 ----
            fn_name = None
            for candidate in ("GenerateKeyEx", "GenerateKey", "generate_key"):
                if hasattr(dll, candidate):
                    fn_name = candidate
                    break
            if fn_name is None:
                exports = []
                try:
                    exports = self._list_pe_exports(file_path)
                except Exception:
                    pass
                self._last_error = (
                    "DLL未导出支持的密钥计算函数。\n"
                    "支持的导出函数: GenerateKeyEx / GenerateKey / generate_key\n"
                    + (f"该DLL实际导出: {', '.join(exports)}" if exports
                       else "无法读取该DLL的导出表"))
                self._logger.error(f"DLL缺少支持的导出函数: {file_path}")
                return None

            # ---- 安全等级: get_security_level导出 > 默认1 ----
            level = 1
            try:
                get_level = dll.get_security_level
                get_level.restype = ctypes.c_int
                level = int(get_level())
            except (AttributeError, ValueError, OSError):
                pass

            # ---- 构造适配器 ----
            if fn_name in ("GenerateKeyEx", "GenerateKey"):
                instance = _GenerateKeyExAlgorithm(dll, level, fn_name)
            else:
                instance = _DllAlgorithm(dll)

            info = SecurityAlgorithmInfo(
                name=os.path.basename(file_path),
                description=f"DLL算法插件 ({fn_name})",
                level=level,
                file_path=file_path,
                is_dll=True,
            )
            info.instance = instance
            self._algorithms[info.level] = info
            self._logger.info(
                f"加载DLL算法: {info.name} (Level={level}, 接口={fn_name}) from {file_path}")
            return info

        except OSError as e:
            err_code = getattr(e, "winerror", None)
            if err_code == 193:
                self._last_error = (
                    f"DLL位数与当前Python不匹配（32/64位冲突）。\n"
                    f"当前Python为{py_bits}位，请确认该DLL是否为{py_bits}位编译。")
            elif err_code == 126:
                self._last_error = (
                    "找不到该DLL依赖的其他DLL。\n"
                    "请将依赖的DLL放到同一目录，或安装相应的运行库(如VC++ Redistributable)。")
            else:
                self._last_error = f"DLL加载失败: {e}"
            self._logger.error(f"加载DLL失败 [{file_path}]: {self._last_error}")
            return None
        except Exception as e:
            self._last_error = f"DLL加载失败: {e}"
            self._logger.error(f"加载DLL失败 [{file_path}]: {e}")
            return None

    def _load_dll_via_bridge(self, file_path: str, dll_bits: int,
                             py_bits: int) -> Optional[SecurityAlgorithmInfo]:
        """位数与当前Python不匹配的DLL，通过桥接子进程加载（典型: 32位DLL + 64位Python）"""
        if self._bridge is None:
            self._bridge = BridgeManager()
        try:
            handle, fn_name, level = self._bridge.load(file_path)
        except BridgeError as e:
            self._last_error = str(e)
            self._logger.error(f"桥接加载DLL失败 [{file_path}]: {e}")
            return None

        info = SecurityAlgorithmInfo(
            name=os.path.basename(file_path),
            description=f"{dll_bits}位DLL算法插件 ({fn_name}, 桥接进程)",
            level=level,
            file_path=file_path,
            is_dll=True,
        )
        info.instance = _BridgedAlgorithm(self._bridge, handle)
        self._algorithms[info.level] = info
        self._logger.info(
            f"桥接加载DLL算法: {info.name} (Level={level}, 接口={fn_name}) from {file_path}")
        return info

    def set_python32_path(self, path: str) -> bool:
        """手动指定32位Python路径（供桥接加载使用），有效则持久化"""
        if self._bridge is None:
            self._bridge = BridgeManager()
        return self._bridge.set_python32_path(path)

    def python32_path(self) -> str:
        """当前已指定/探测到的32位Python路径（空表示未找到）"""
        if self._bridge is None:
            self._bridge = BridgeManager()
        return self._bridge.python32

    def shutdown(self):
        """关闭桥接子进程（程序退出时调用）"""
        if self._bridge is not None:
            self._bridge.shutdown()

    @staticmethod
    def _list_pe_exports(file_path: str) -> list[str]:
        """读取PE文件导出函数名列表（仅用于错误提示）"""
        with open(file_path, "rb") as f:
            data = f.read()
        pe_off = struct.unpack_from("<I", data, 0x3C)[0]
        opt_off = pe_off + 24
        magic = struct.unpack_from("<H", data, opt_off)[0]
        dd_off = opt_off + (112 if magic == 0x20B else 96)
        exp_rva = struct.unpack_from("<I", data, dd_off)[0]
        if exp_rva == 0:
            return []

        # 节表（用于RVA转文件偏移）
        num_sec = struct.unpack_from("<H", data, pe_off + 6)[0]
        opt_size = struct.unpack_from("<H", data, pe_off + 20)[0]
        sec_off = opt_off + opt_size
        sections = []
        for i in range(num_sec):
            off = sec_off + i * 40
            vaddr, vsize, rawsize, rawoff = struct.unpack_from("<IIII", data, off + 12)
            sections.append((vaddr, max(vsize, rawsize), rawoff))

        def rva2off(rva):
            for vaddr, size, rawoff in sections:
                if vaddr <= rva < vaddr + size:
                    return rawoff + (rva - vaddr)
            return None

        exp_off = rva2off(exp_rva)
        if exp_off is None:
            return []
        num_names = struct.unpack_from("<I", data, exp_off + 24)[0]
        npt_rva = struct.unpack_from("<I", data, exp_off + 36)[0]
        npt_off = rva2off(npt_rva)
        if npt_off is None:
            return []
        names = []
        for i in range(min(num_names, 64)):
            name_rva = struct.unpack_from("<I", data, npt_off + i * 4)[0]
            name_off = rva2off(name_rva)
            if name_off is None:
                continue
            end = data.index(b"\x00", name_off)
            names.append(data[name_off:end].decode(errors="replace"))
        return names

    def unload_algorithm(self, level: int) -> bool:
        """卸载指定等级的算法"""
        if level in self._algorithms:
            info = self._algorithms.pop(level)
            self._logger.info(f"卸载安全算法: {info.name} (Level={level})")
            return True
        return False

    def generate_key(self, level: int, seed: bytes) -> Optional[bytes]:
        """使用指定等级的算法计算密钥

        Args:
            level: 安全等级
            seed: ECU返回的种子

        Returns:
            计算得到的密钥，失败返回None
        """
        if level not in self._algorithms:
            self._logger.error(f"未加载Level {level}的安全算法")
            return None

        info = self._algorithms[level]

        try:
            # 统一接口: Python插件实例和DLL适配器(_DllAlgorithm)都提供 generate_key(seed)
            if info.instance is None or not hasattr(info.instance, 'generate_key'):
                self._logger.error(f"算法实例无效: Level {level}")
                return None

            key = info.instance.generate_key(seed)
            if not key:
                self._logger.error(f"密钥计算返回空结果: Level {level}")
                return None
            self._logger.info(
                f"Level {level} 密钥计算: seed={seed.hex()}, key={key.hex()}")
            return key

        except Exception as e:
            self._logger.error(f"密钥计算失败 [Level {level}]: {e}")
            return None

    def test_algorithm_offline(self, level: int, test_seed: bytes) -> dict:
        """离线测试算法（无需连接ECU）

        Args:
            level: 安全等级
            test_seed: 测试种子

        Returns:
            测试结果字典
        """
        start_time = time.time()
        key = self.generate_key(level, test_seed)
        elapsed = time.time() - start_time

        return {
            "success": key is not None,
            "level": level,
            "seed": test_seed,
            "key": key,
            "elapsed_ms": elapsed * 1000,
        }

    def scan_plugins_directory(self) -> list[str]:
        """扫描插件目录，返回可用的算法文件列表"""
        files = []
        if not os.path.exists(self._plugins_dir):
            return files

        for f in os.listdir(self._plugins_dir):
            # 跳过隐藏文件/基类/测试产物（test_前缀）
            if f.startswith('_') or f.startswith('test_') or f == 'algorithm_base.py':
                continue
            if f.endswith(('.py', '.dll')):
                files.append(os.path.join(self._plugins_dir, f))
        return files

    def load_all_plugins(self) -> list[SecurityAlgorithmInfo]:
        """加载插件目录中的所有算法"""
        loaded = []
        for file_path in self.scan_plugins_directory():
            info = self.load_algorithm_file(file_path)
            if info:
                loaded.append(info)
        return loaded

    # --- 配置档案管理 ---

    def save_config(self, config_name: str, config: dict):
        """保存算法配置档案"""
        self._configs[config_name] = config
        self._logger.info(f"保存安全算法配置: {config_name}")

    def load_config(self, config_name: str) -> Optional[dict]:
        """加载算法配置档案"""
        return self._configs.get(config_name)

    def list_configs(self) -> list[str]:
        """列出所有配置档案"""
        return list(self._configs.keys())

    # --- 解锁状态管理 ---

    def mark_unlocked(self, level: int, timeout_s: float = 30.0):
        """标记某等级已解锁，记录超时时间"""
        self._unlock_timers[level] = time.time() + timeout_s

    def is_unlocked(self, level: int) -> bool:
        """检查某等级是否已解锁且未超时"""
        if level not in self._unlock_timers:
            return False
        return time.time() < self._unlock_timers[level]

    def unlocked_levels(self) -> list:
        """当前处于解锁且未超时状态的所有等级"""
        return [lvl for lvl in self._unlock_timers if self.is_unlocked(lvl)]

    def get_unlock_remaining_time(self, level: int) -> float:
        """获取解锁剩余时间（秒）"""
        if level not in self._unlock_timers:
            return 0.0
        remaining = self._unlock_timers[level] - time.time()
        return max(0.0, remaining)
