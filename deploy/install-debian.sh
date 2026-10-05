#!/bin/bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$(id -u)" -eq 0 ]; then
    echo 'Execute como o usuario normal. O sudo sera usado apenas para os pacotes e o servico.' >&2
    exit 1
fi
if ! python3 -m venv "$project_dir/.venv"; then
    sudo apt-get update
    sudo apt-get install -y python3-venv
    python3 -m venv "$project_dir/.venv"
fi
"$project_dir/.venv/bin/python" -m pip install -e "$project_dir[web]"
config_path="${XDG_CONFIG_HOME:-$HOME/.config}/drivebridge"
install -d -m 700 "$config_path"
if [ ! -f "$config_path/web.env" ]; then
    umask 077
    cat > "$config_path/web.env" <<'ENV'
DRIVEBRIDGE_BIND=127.0.0.1
DRIVEBRIDGE_PORT=8768
DRIVEBRIDGE_PUBLIC_URL=http://localhost:8768
DRIVEBRIDGE_ALLOWED_HOSTS=localhost,127.0.0.1
ENV
fi
"$project_dir/.venv/bin/python" -c 'from drivebridge.web import create_app; create_app()'
# Adapta a unidade para o usuario e a pasta desta instalacao.
"$project_dir/.venv/bin/python" - "$project_dir" "$config_path" <<'PY'
import getpass
import grp
import os
import sys
from pathlib import Path
project, config = map(Path, sys.argv[1:])
user = getpass.getuser()
group = grp.getgrgid(os.getgid()).gr_name
text = (project / 'deploy/drivebridge.service').read_text()
text = text.replace('@USER@', user).replace('@GROUP@', group)
text = text.replace('@PROJECT_DIR@', str(project)).replace('@CONFIG_DIR@', str(config))
(project / 'deploy/drivebridge-installed.service').write_text(text)
PY
sudo install -m 644 "$project_dir/deploy/drivebridge-installed.service" /etc/systemd/system/drivebridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now drivebridge.service
sudo systemctl status drivebridge.service --no-pager
printf '\nPagina interna: http://localhost:8768\nSenha inicial guardada em: %s/web-password.txt\n' "$config_path"
