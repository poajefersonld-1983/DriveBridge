#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
python3 -m venv "$project_dir/.venv"
"$project_dir/.venv/bin/python" -m pip install -e "$project_dir"
"$project_dir/.venv/bin/python" - "$project_dir" <<'PY'
import os
import subprocess
import sys
from pathlib import Path
project = Path(sys.argv[1]).resolve()
entry = ('[Desktop Entry]\nType=Application\nName=DriveBridge\n'
         'Comment=Sincronização pessoal com Google Drive\n'
         f'Exec="{str(project / "iniciar.sh").replace(chr(34), chr(92)+chr(34))}"\n'
         'Icon=folder-remote\nTerminal=false\nCategories=Network;Utility;\n')
applications = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'applications'
applications.mkdir(parents=True, exist_ok=True)
(applications/'drivebridge.desktop').write_text(entry)
try:
    value = subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip()
    desktop = Path(value) if value else None
except (OSError, subprocess.CalledProcessError):
    desktop = Path.home()/'Desktop'
if desktop and desktop.is_dir() and desktop != Path.home():
    shortcut = desktop/'DriveBridge.desktop'
    shortcut.write_text(entry)
    shortcut.chmod(0o755)
print('Atalho instalado no menu de aplicativos. Abra DriveBridge para conectar a sua própria conta.')
PY
