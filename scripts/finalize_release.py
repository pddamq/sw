import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = ROOT / 'dist' / 'FormaCAD'
    if not (app / 'FormaCAD.exe').exists():
        raise SystemExit('Build the desktop executable before finalizing the release.')
    # The desktop CLR loads bundled WebView2 assemblies. Scope the configuration to this executable.
    shutil.copy2(ROOT / 'packaging' / 'netfx.config', app / 'FormaCAD.exe.config')
    shutil.copy2(ROOT / 'README.md', app / 'README.md')
    shutil.copy2(ROOT / 'docs' / 'ACCEPTANCE.md', app / 'ACCEPTANCE.md')
    for name in ('BUILTIN_EXECUTOR.md', 'FULL_EXECUTOR.md', 'BLENDER_MCP.md', 'EXTENSIONS.md', 'DSH_INTEGRATION.md'):
        if (ROOT / 'docs' / name).exists():
            shutil.copy2(ROOT / 'docs' / name, app / name)
    if (ROOT / 'docs' / 'VALIDATION.md').exists():
        shutil.copy2(ROOT / 'docs' / 'VALIDATION.md', app / 'VALIDATION.md')
    print('Release documentation and desktop runtime configuration copied.')


if __name__ == '__main__':
    main()
