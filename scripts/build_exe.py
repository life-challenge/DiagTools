"""DiagTools 可执行文件构建脚本

用法（在项目根目录）:
    python scripts/build_exe.py            # 构建到 dist/DiagTools/
    python scripts/build_exe.py --clean    # 先清理 build/dist 再构建

产物结构（onedir，整个 DiagTools 文件夹即为发布单元）:
    dist/DiagTools/
    ├── DiagTools.exe          # 主程序（双击运行，无需安装Python）
    ├── _internal/             # PyInstaller 运行时依赖
    ├── resources/             # 配置/ECU定义/DID定义（可编辑）
    ├── plugins/               # 安全算法插件（Python示例；DLL按需放入）
    ├── bridge_worker.py       # 32位DLL桥接脚本（外部Python执行）
    ├── README.md / LICENSE
    └── （运行后生成 logs/ data_recordings/ reports/）

分发给他人: 将整个 DiagTools 文件夹压缩发送即可。
"""

import argparse
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_APP_DIR = os.path.join(PROJECT_ROOT, "dist", "DiagTools")

# 需要复制到exe旁边的运行时数据（源路径, 目标相对路径）
_COPY_ITEMS = [
    ("resources", "resources"),
    ("plugins", "plugins"),
    ("src/business/bridge_worker.py", "bridge_worker.py"),
    ("README.md", "README.md"),
    ("LICENSE", "LICENSE"),
]


def _copy_runtime_data():
    """复制资源/插件/桥接脚本到发行目录"""
    copied = 0
    for src_rel, dst_rel in _COPY_ITEMS:
        src = os.path.join(PROJECT_ROOT, src_rel)
        dst = os.path.join(DIST_APP_DIR, dst_rel)
        if not os.path.exists(src):
            print(f"  [跳过] {src_rel} 不存在")
            continue
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(
                src, dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        copied += 1
        print(f"  [复制] {src_rel} -> {dst_rel}")

    # 清理上一次运行生成的工作数据（发行目录保持干净）
    for stale in ("logs", "data_recordings", "reports"):
        stale_path = os.path.join(DIST_APP_DIR, stale)
        if os.path.exists(stale_path):
            shutil.rmtree(stale_path)
            print(f"  [清理] 移除旧的 {stale}/")
    return copied


def main():
    parser = argparse.ArgumentParser(description="DiagTools 打包构建")
    parser.add_argument("--clean", action="store_true",
                        help="构建前清理 build/ 与 dist/")
    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)

    if args.clean:
        for d in ("build", "dist"):
            path = os.path.join(PROJECT_ROOT, d)
            if os.path.exists(path):
                shutil.rmtree(path)
                print(f"[清理] 已删除 {d}/")

    print("[1/3] PyInstaller 打包中...")
    ret = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm",
         "DiagTools.spec"],
        cwd=PROJECT_ROOT)
    if ret.returncode != 0:
        print("PyInstaller 构建失败")
        sys.exit(ret.returncode)

    print("[2/3] 复制运行时数据...")
    _copy_runtime_data()

    exe = os.path.join(DIST_APP_DIR, "DiagTools.exe")
    if not os.path.exists(exe):
        print(f"错误: 未找到 {exe}")
        sys.exit(1)

    print(f"[3/3] 构建完成: {exe}")
    try:
        total_mb = sum(
            os.path.getsize(os.path.join(r, f))
            for r, _, fs in os.walk(DIST_APP_DIR) for f in fs) / 1024 / 1024
        print(f"发行目录总大小: {total_mb:.1f} MB")
    except OSError:
        pass


if __name__ == "__main__":
    main()
