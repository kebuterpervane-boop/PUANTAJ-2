# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('docs/KULLANICI_KILAVUZU.md', '.')],
    hiddenimports=['core', 'core.database', 'core.hesaplama', 'core.signals', 'core.version', 'core.user_config', 'core.app_logger', 'core.update_check', 'core.input_validators', 'pages', 'pages.dashboard', 'pages.records', 'pages.settings', 'pages.personnel', 'pages.payslip', 'pages.upload', 'pages.raporlar', 'pages.avans', 'pages.bes', 'pages.izin', 'pages.holidays', 'pages.vardiya', 'pages.disiplin', 'migrations', 'migrations.migrations', 'migrations.ay_kilit', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtPrintSupport', 'openpyxl', 'pandas', 'reportlab', 'sqlite3', 'bcrypt'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pytest', 'IPython', 'notebook', 'tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SaralPuantaj_Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
