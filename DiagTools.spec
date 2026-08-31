# -*- mode: python ; coding: utf-8 -*-
"""DiagTools PyInstaller 打包配置（onedir 模式）

资源策略:
  - 代码与依赖打进 dist/DiagTools/
  - resources/、plugins/、bridge_worker.py 不打进exe，由构建脚本
    复制到 exe 旁边 —— 配置可编辑、插件可替换、32位桥接脚本
    以文件形态存在（外部Python进程直接执行该文件）
  - 运行时 logs/ data_recordings/ reports/ 生成在 exe 旁边（见 src/utils/paths.py）

构建: python scripts/build_exe.py
"""

import os

block_cipher = None

# python-can 硬件接口为运行时动态导入，需显式声明
hiddenimports = [
    'can.interfaces.virtual',
    'can.interfaces.pcan',
    'can.interfaces.vector',
    'can.interfaces.ixxat',
    'can.interfaces.socketcand',
    'can.interfaces.systec',
]

# 排除未使用的大组件，减小体积
excludes = [
    'tkinter',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtQuick',
    'PyQt6.QtQml',
    'PyQt6.QtBluetooth',
    'PyQt6.QtNfc',
    'PyQt6.QtPositioning',
    'PyQt6.QtMultimedia',
    'PyQt6.QtMultimediaWidgets',
    'PyQt6.QtPdf',
    'PyQt6.QtPdfWidgets',
    'PyQt6.QtSql',
    'PyQt6.QtTest',
    'PyQt6.QtDesigner',
    'PyQt6.QtCharts',
    'PyQt6.QtDataVisualization',
    'PyQt6.Qt3DCore',
]

PROJECT_ROOT = os.path.dirname(os.path.abspath(SPEC))
ICON = os.path.join(PROJECT_ROOT, 'resources', 'icons', 'diagtools.ico')

a = Analysis(
    ['main.py'],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DiagTools',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI程序，无控制台
    disable_windowed_traceback=False,
    icon=ICON if os.path.exists(ICON) else None,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DiagTools',
)
