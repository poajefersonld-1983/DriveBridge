"""Confere arquivos rastreados/preparados para publicação, sem imprimir segredos."""
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
try:
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root, stderr=subprocess.DEVNULL).decode().split('\0')
    paths = [root/name for name in names if name]
except (OSError, subprocess.CalledProcessError):
    paths = [p for p in root.rglob('*') if p.is_file() and not any(part in {'.git','.venv','.deployment','__pycache__','dist','build'} or part.endswith('.egg-info') for part in p.relative_to(root).parts)]
private_names = {'accounts.json','profiles.json','email-settings.json','web-settings.json','web-password.txt','web.env','oauth-client.json','oauth-web-client.json','credentials.json','token.json'}
private_dirs = {'.deployment','tokens','states','reports','email-outbox','.config','.venv'}
patterns = [r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', r'\bya29\.[A-Za-z0-9_-]+', r'\b1//[A-Za-z0-9_-]{20,}', r'\bgh[pousr]_[A-Za-z0-9]{20,}', r'\bAIza[A-Za-z0-9_-]{30,}', r'\bGOCSPX-[A-Za-z0-9_-]+']
issues = []
for path in paths:
    relative = path.relative_to(root)
    if path.is_symlink() or path.name in private_names or any(part in private_dirs for part in relative.parts):
        issues.append((str(relative),'arquivo privado ou link simbólico'))
        continue
    data = path.read_bytes()
    if any(re.search(pattern,data.decode(errors='replace')) for pattern in patterns):
        issues.append((str(relative),'padrão de credencial encontrado'))
for name, reason in issues:
    print(f'{name}: {reason}')
if issues:
    sys.exit(1)
print(f'Auditoria concluída: {len(paths)} arquivos; nenhum arquivo privado ou padrão de credencial encontrado.')
