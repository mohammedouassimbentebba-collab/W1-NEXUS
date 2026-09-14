# W1 Nexus deterministic PyInstaller entrypoint.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
repo = Path(SPECPATH).resolve().parents[1]
entrypoint = repo / "src" / "w1cip" / "native_entrypoint.py"
icon = repo / "brand" / "production" / "w1-nexus.ico"
version_info = repo / "packaging" / "windows" / "version-info.txt"
datas = collect_data_files("w1cip")
a = Analysis([str(entrypoint)], pathex=[str(repo)], binaries=[], datas=datas, hiddenimports=["w1cip"], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="W1 Nexus", icon=str(icon), version=str(version_info), debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="W1 Nexus")
