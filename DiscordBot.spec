# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Collect any hidden submodules (if needed)
hidden_submodules = collect_submodules('discord')
# Collect submodules for MetaTrader5 if necessary
hidden_submodules += collect_submodules('MetaTrader5')

# Collect any necessary data files for the libraries (e.g., for discord)
discord_data = collect_data_files('discord')
# Include any necessary data files for MetaTrader5 if required
mt5_data = collect_data_files('MetaTrader5')

a = Analysis(
    ['DiscordBot.py'],
    pathex=['.'],  # Explicitly set the script's path to the current directory or where your script is located
    binaries=[],
    datas=discord_data + mt5_data,  # Include discord's and MetaTrader5's data files if needed
    hiddenimports=['numpy', 'numpy.core', 'numpy.core.multiarray', 'discord', 'MetaTrader5', 're', 'datetime', 'timedelta'] + hidden_submodules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='DiscordBot',
    debug=False,  # Enable debug mode for more detailed output
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
