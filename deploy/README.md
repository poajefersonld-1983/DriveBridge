# Servidor pessoal no Debian

Flask + Waitress servem o painel, e systemd mantém o serviço e o agendamento ativos. A instalação é feita com o usuário normal; sudo é usado para os pacotes necessários e a unidade systemd.

## Instalação

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv
git clone https://github.com/poajefersonld-1983/DriveBridge.git
cd DriveBridge
./deploy/install-debian.sh
```

O instalador cria `.venv`, instala `.[web]`, gera senha e configura `drivebridge.service`. Ele não inclui nem importa uma conta Google. No servidor sem interface gráfica, não é necessário instalar Tkinter.

Configuração: `~/.config/drivebridge/web.env`. Padrão:

```ini
DRIVEBRIDGE_BIND=127.0.0.1
DRIVEBRIDGE_PORT=8768
DRIVEBRIDGE_PUBLIC_URL=http://localhost:8768
DRIVEBRIDGE_ALLOWED_HOSTS=localhost,127.0.0.1
```

Senha inicial: `~/.config/drivebridge/web-password.txt`. Não publique esse arquivo. Quem conhece a senha do painel acessa todas as contas/pares daquela instalação e pode navegar pelas pastas acessíveis ao usuário do serviço.

## Acesso remoto seguro por SSH

No computador onde abrirá o navegador, substitua `SEU_USUARIO` e `SEU_SERVIDOR`:

```bash
ssh -N -L 8768:127.0.0.1:8768 SEU_USUARIO@SEU_SERVIDOR
```

Abra **http://localhost:8768**. O serviço permanece restrito ao localhost do servidor; não é necessário abrir a porta na internet.

## Adicionar conta Google no servidor

1. Crie seu próprio projeto Google Cloud, habilite Drive API, configure consentimento e usuário de teste.
2. Crie um cliente OAuth **Aplicativo para computador**, baixe o JSON e abra o painel pelo túnel SSH acima.
3. Entre no painel, clique em **Adicionar conta**, importe o JSON, informe e-mail e autorize no navegador.
4. O retorno OAuth deve chegar ao mesmo endereço `http://localhost:8768` usado pelo navegador.

Para um domínio com HTTPS, registre um cliente OAuth **web** com retorno `https://seu-dominio/oauth/callback`, defina esse domínio em `DRIVEBRIDGE_PUBLIC_URL` (sem `/oauth/callback`) e em `DRIVEBRIDGE_ALLOWED_HOSTS`. Importe o JSON web nas opções avançadas. Ele será salvo separadamente do cliente desktop.

## Acesso na rede local

Se realmente quiser acesso direto na LAN, edite `web.env` e use o IP privado do seu próprio servidor:

```ini
DRIVEBRIDGE_BIND=IP_DO_SEU_SERVIDOR
DRIVEBRIDGE_PORT=8768
DRIVEBRIDGE_PUBLIC_URL=http://localhost:8768
DRIVEBRIDGE_ALLOWED_HOSTS=localhost,127.0.0.1,IP_DO_SEU_SERVIDOR
```

Reinicie o serviço e abra `http://IP_DO_SEU_SERVIDOR:8768`. Ajuste o firewall para permitir somente a rede desejada. Para adicionar contas com cliente desktop, continue usando o túnel SSH em localhost; o login OAuth exige o endereço canônico configurado. Para exposição por domínio, use HTTPS e cliente OAuth web. Não abra o painel pessoal diretamente na internet.

Se a porta estiver ocupada, altere **porta, URL pública e túnel** de modo consistente. Para HTTPS, coloque um proxy reverso com certificado válido na frente do Waitress, mantendo o backend no localhost. A configuração do proxy depende do seu ambiente.

## Operação e atualização

```bash
sudo systemctl status drivebridge --no-pager
sudo journalctl -u drivebridge -n 50 --no-pager
sudo systemctl stop drivebridge
sudo systemctl start drivebridge
sudo systemctl restart drivebridge
```

Antes de atualizar/reiniciar, aguarde a operação terminar ou use **Cancelar** e confira que ficou ociosa. A parada do serviço cancela a sincronização em andamento; arquivos concluídos são preservados.

```bash
sudo systemctl stop drivebridge
git pull --ff-only
.venv/bin/python -m pip install -e '.[web]'
.venv/bin/python -m unittest discover -s tests -q
sudo systemctl start drivebridge
```

Faça backup privado da configuração antes de atualizar. O instalador não sobrescreve `web.env` existente. Se mudar a localização da pasta do projeto, execute novamente o instalador para atualizar os caminhos da unidade.

## Migrar seus próprios dados

Não copie a instalação de outra pessoa. Para migrar a sua, faça backup privado na origem e no destino e transfira a configuração via SSH: contas, tokens, pares, checkpoints, agenda, cliente OAuth e, se desejado, configuração SMTP. Não use o repositório Git como transporte dessas credenciais.

Os caminhos locais devem existir no servidor. Se trocar o caminho, a identidade do checkpoint muda e o motor trata o par como primeira sincronização; divergências são sinalizadas como conflitos. O seletor de pasta navega pelo servidor, não pelo computador do navegador.

## E-mail e registros

Configure o remetente SMTP no botão **Relatórios por e-mail**, salve e envie um teste. O destinatário é a conta Google do par. A fila continua após reinícios, tenta novamente e registra aceite ou falha SMTP na atividade. Não coloque senhas em comandos compartilhados ou na documentação.

Arquivos privados e logs ficam em `~/.config/drivebridge`. `activity.log` tem rotação; `reports/` e `email-outbox/` não têm limpeza automática. Monitore espaço em disco e arquive os relatórios conforme sua necessidade.

## Desinstalar o serviço

```bash
sudo systemctl disable --now drivebridge
sudo rm /etc/systemd/system/drivebridge.service
sudo systemctl daemon-reload
```

Esses comandos removem apenas a unidade do serviço. O projeto, seus arquivos sincronizados e a configuração privada permanecem para recuperação. Não apague tokens/configurações ou arquivos sincronizados sem seu próprio backup.
