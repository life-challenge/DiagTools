"""32位桥接工作进程（纯标准库实现，运行于32位Python）

当主程序（64位Python）需要加载32位安全算法DLL时，会启动本工作进程，
在其中加载DLL并计算密钥。通信采用 stdin/stdout 逐行JSON协议：

请求:  {"id": <int>, "cmd": "load"|"generate_key"|"shutdown", ...}
响应:  {"id": <int>, "ok": true/false, ...}

支持的命令:
  load:         {"dll_path": str, "stdcall": bool?}
                -> {"ok": true, "handle": int, "fn_name": str, "level": int}
  generate_key: {"handle": int, "seed_hex": str, "level": int?}
                （level为实际请求的安全等级，缺省用加载时声明的等级）
                -> {"ok": true, "key_hex": str}
  shutdown:     响应后退出进程

注意: 本文件只依赖Python标准库（32位Python环境无需安装任何第三方包）。
"""

import ctypes
import json
import os
import sys
import traceback

# 协议固定使用UTF-8，避免Windows控制台默认GBK导致中文乱码/解码错误（3.7+）
try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

KEY_BUF_SIZE = 64  # 密钥输出缓冲区上限（UDS密钥通常不超过16字节）

# handle -> {"fn": 导出函数, "kind": "simple"|"keyex", "level": int, "with_options": bool}
_handles = {}
_next_handle = 1


def _cmd_load(req):
    """加载DLL并探测导出函数，返回句柄"""
    global _next_handle

    dll_path = req.get("dll_path", "")
    if not dll_path or not os.path.exists(dll_path):
        return {"ok": False, "error": "DLL文件不存在: %s" % dll_path}
    dll_path = os.path.abspath(dll_path)

    # 将DLL所在目录加入依赖搜索路径
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(os.path.dirname(dll_path))
        except OSError:
            pass

    stdcall = bool(req.get("stdcall", False))
    loader = ctypes.WinDLL if (stdcall and hasattr(ctypes, "WinDLL")) else ctypes.CDLL
    dll = loader(dll_path)

    # 探测导出函数（按优先级）
    fn_name = None
    for candidate in ("GenerateKeyEx", "GenerateKey", "generate_key"):
        if hasattr(dll, candidate):
            fn_name = candidate
            break
    if fn_name is None:
        return {"ok": False,
                "error": "DLL未导出支持的密钥计算函数 (GenerateKeyEx/GenerateKey/generate_key)"}

    # 安全等级: get_security_level导出 > 默认1（零参数函数不受调用约定影响）
    level = 1
    try:
        get_level = dll.get_security_level
        get_level.restype = ctypes.c_int
        level = int(get_level())
    except (AttributeError, ValueError, OSError):
        pass

    entry = {"level": level}
    if fn_name == "generate_key":
        fn = dll.generate_key
        fn.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_int),
        ]
        fn.restype = ctypes.c_int
        entry["kind"] = "simple"
    else:
        fn = getattr(dll, fn_name)
        fn.restype = ctypes.c_ulong
        # 回退候选签名: [是否带options]；部分供应商DLL（如TP_DASH）无options参数，
        # 传options会把key缓冲区位移（典型症状: access violation writing 0x40）。
        # 显式旧版GenerateKey直接用7参不回退；no_options=主进程已知该DLL无options参数。
        entry["variants"] = [False] if fn_name == "GenerateKey" else [True, False]
        entry["variant_idx"] = 0
        if req.get("no_options") and len(entry["variants"]) > 1:
            entry["variant_idx"] = 1
        _apply_keyex_argtypes(fn, entry)
        entry["kind"] = "keyex"
    entry["fn"] = fn

    handle = _next_handle
    _next_handle += 1
    _handles[handle] = entry
    return {"ok": True, "handle": handle, "fn_name": fn_name, "level": level}


def _apply_keyex_argtypes(fn, entry):
    """按当前候选签名设置GenerateKeyEx/GenerateKey的argtypes"""
    with_options = entry["variants"][entry["variant_idx"]]
    argtypes = [
        ctypes.c_char_p, ctypes.c_ulong,    # seed, seed_size
        ctypes.c_ulong, ctypes.c_char_p,    # security_level, variant
    ]
    if with_options:
        argtypes.append(ctypes.c_char_p)    # options
    argtypes += [
        ctypes.c_char_p, ctypes.c_ulong,    # key_buf, max_key_size
        ctypes.POINTER(ctypes.c_ulong),     # actual_key_size
    ]
    fn.argtypes = argtypes


def _keyex_call_once(fn, entry, seed, level=None):
    # level为实际请求的安全等级（透传给GenerateKeyEx），缺省用声明的等级
    lvl = entry["level"] if level is None else level
    key_buf = ctypes.create_string_buffer(KEY_BUF_SIZE)
    actual = ctypes.c_ulong(0)
    args = [bytes(seed), len(seed), lvl, b"default"]
    if entry["variants"][entry["variant_idx"]]:
        args.append(b"")
    args += [key_buf, KEY_BUF_SIZE, ctypes.byref(actual)]
    ret = fn(*args)
    if ret != 0:
        return None, "DLL密钥计算返回错误码: %d" % ret
    if not 0 <= actual.value <= KEY_BUF_SIZE:
        return None, "DLL返回的密钥长度无效: %d" % actual.value
    return key_buf.raw[:actual.value], ""


def _cmd_generate_key(req):
    """使用已加载的DLL计算密钥"""
    handle = req.get("handle")
    entry = _handles.get(handle)
    if entry is None:
        return {"ok": False, "error": "无效的DLL句柄: %s" % handle}

    try:
        seed = bytes.fromhex(req.get("seed_hex", ""))
    except ValueError:
        return {"ok": False, "error": "seed_hex不是有效的十六进制字符串"}
    if not seed:
        return {"ok": False, "error": "种子为空"}
    level = req.get("level")  # 实际请求的安全等级（可缺省）

    fn = entry["fn"]
    if entry["kind"] == "simple":
        seed_buf = (ctypes.c_ubyte * len(seed))(*seed)
        key_buf = (ctypes.c_ubyte * KEY_BUF_SIZE)()
        key_len = ctypes.c_int(KEY_BUF_SIZE)
        ret = fn(seed_buf, len(seed), key_buf, ctypes.byref(key_len))
        if ret != 0:
            return {"ok": False, "error": "DLL generate_key 返回错误码: %d" % ret}
        if not 0 <= key_len.value <= KEY_BUF_SIZE:
            return {"ok": False, "error": "DLL返回的密钥长度无效: %d" % key_len.value}
        key = bytes(key_buf[:key_len.value])
    else:
        key = None
        err = ""
        fallback = False
        try:
            key, err = _keyex_call_once(fn, entry, seed, level)
        except OSError:
            # access violation: 签名不匹配，切换无options的7参版本重试一次；
            # 子进程隔离，崩溃可接受；重试成功则告知主进程记住该签名。
            if entry["variant_idx"] + 1 < len(entry["variants"]):
                entry["variant_idx"] += 1
                _apply_keyex_argtypes(fn, entry)
                key, err = _keyex_call_once(fn, entry, seed, level)
                fallback = key is not None
            else:
                raise
        if key is None:
            return {"ok": False, "error": err or "DLL密钥计算失败"}
        return {"ok": True, "key_hex": key.hex(), "fallback": fallback}

    return {"ok": True, "key_hex": key.hex()}


def _write(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id", 0)
        cmd = req.get("cmd")
        try:
            if cmd == "load":
                resp = _cmd_load(req)
            elif cmd == "generate_key":
                resp = _cmd_generate_key(req)
            elif cmd == "shutdown":
                _write({"id": rid, "ok": True})
                return
            else:
                resp = {"ok": False, "error": "未知命令: %s" % cmd}
        except Exception as e:
            resp = {"ok": False, "error": str(e),
                    "trace": traceback.format_exc(limit=5)}
        resp["id"] = rid
        _write(resp)


if __name__ == "__main__":
    main()
