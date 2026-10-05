# DriveBridge

Cliente de sincronização do Google Drive para Linux, escrito em Python, com interface desktop e painel web para um servidor pessoal. Usa diretamente a Google Drive API v3, sem rclone.

**Versão inicial para a comunidade.** Cada instalação usa suas próprias contas e configurações. Este repositório não inclui contas Google, clientes OAuth, tokens, senhas, pastas pessoais ou logs reais.

## Recursos

- Múltiplas contas Google e pares de pastas locais/remotas.
- Local → Drive, Drive → Local e sincronização bidirecional.
- Navegação pelas pastas locais do servidor e pastas remotas do Drive. `root` representa todo o Meu Drive.
- Progresso por bytes confirmados, arquivos ativos, taxa total e média.
- Até 8 arquivos simultâneos, com blocos de 4, 8, 16 ou 32 MiB no painel web. Padrão: 4 arquivos × 16 MiB. Sem limitador de MiB/s no aplicativo.
- Agendamento web por minutos, horas, dias, semanas ou meses; execução manual; início ao ligar o serviço, após um intervalo ou em um horário escolhido.
- Opções web para comparar conteúdo, sobrescrever em uma direção ou copiar a versão mais recente pela data.
- Relatórios SMTP ao terminar a operação, inclusive falhas e cancelamentos, para o e-mail da conta Google daquele par. Resumo no corpo e log completo compactado no anexo.

## Escolha como executar

| Modo | Uso | Agendamento | E-mail |
| --- | --- | --- | --- |
| Desktop (Tkinter) | Computador Linux com interface gráfica | Intervalos em minutos, enquanto aberto | Não possui configuração SMTP na interface desktop |
| Web (Flask + Waitress) | Painel pessoal no computador ou servidor Debian | Calendário e intervalos, mesmo com navegador fechado | SMTP e fila persistente |

O painel web desta versão é **pessoal, com uma senha de acesso**, não um serviço com usuários isolados. Contas e pares adicionados na mesma instalação são compartilhados por quem tem acesso ao painel.

## Baixar e instalar com um comando

**Desktop — Debian/Ubuntu ou Fedora:**

```bash
curl -fsSL https://raw.githubusercontent.com/poajefersonld-1983/DriveBridge/main/install.sh -o drivebridge-install.sh && bash drivebridge-install.sh --desktop
```

**Servidor web — Debian/Ubuntu:**

```bash
curl -fsSL https://raw.githubusercontent.com/poajefersonld-1983/DriveBridge/main/install.sh -o drivebridge-install.sh && bash drivebridge-install.sh --web
```

Execute como usuário normal, com `curl` instalado. O script instala dependências pelo gerenciador de pacotes (solicitando sudo), baixa o projeto para `~/DriveBridge` e configura o modo escolhido. Uma pasta já existente de outro projeto ou com alterações não é sobrescrita. Para escolher outro destino, defina `DRIVEBRIDGE_INSTALL_DIR=/caminho/DriveBridge` ao executar o script.

Você pode ler `drivebridge-install.sh` antes de executá-lo. A instalação não importa configurações de outras pessoas: a primeira conexão Google e a configuração SMTP são feitas por você. O GitHub hospeda o código e esta documentação; não é um serviço de sincronização das contas dos usuários.

## Requisitos e instalação

Python 3.10 ou superior. Testado em Fedora e Debian. Tenha Git e acesso à internet para instalar as dependências Python.

Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-tk
```

Fedora:

```bash
sudo dnf install -y git python3 python3-tkinter
```

Baixe o projeto:

```bash
git clone https://github.com/poajefersonld-1983/DriveBridge.git
cd DriveBridge
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
```

### Desktop

```bash
.venv/bin/python -m pip install -e .
./iniciar.sh
```

Para instalar o atalho no menu de aplicativos e no desktop:

```bash
./tools/install-desktop.sh
```

### Painel web local

```bash
.venv/bin/python -m pip install -e '.[web]'
DRIVEBRIDGE_PORT=8768 DRIVEBRIDGE_PUBLIC_URL=http://localhost:8768 .venv/bin/drivebridge-web
```

Abra **http://localhost:8768**. No primeiro início, a senha do painel é gerada e gravada em `~/.config/drivebridge/web-password.txt` (ou no diretório XDG equivalente). Leia esse arquivo apenas no seu computador. Ela não é a senha da conta Google.

### Serviço no Debian

Para iniciar automaticamente com o Debian:

```bash
./deploy/install-debian.sh
```

O instalador usa o usuário atual, ambiente virtual e systemd. O padrão escuta **somente em 127.0.0.1:8768**. Veja [instalação e administração do servidor](deploy/README.md) para acessar por SSH, habilitar acesso na rede local, trocar a porta, atualizar e desinstalar o serviço.

## Conectar sua própria conta Google

O repositório não traz um cliente OAuth pronto. Cada pessoa configura seu projeto Google Cloud:

1. Crie ou selecione um projeto no [Google Cloud](https://console.cloud.google.com/).
2. Habilite a **Google Drive API** nesse projeto.
3. Configure o consentimento no Google Auth Platform: marca, público e acesso aos dados. Para uso pessoal em teste, adicione sua conta como usuário de teste.
4. Crie um cliente OAuth do tipo **Aplicativo para computador** e baixe o JSON.
5. Abra **Adicionar conta** no DriveBridge, informe seu e-mail e importe o JSON na configuração avançada.
6. Autorize no navegador. O aplicativo salva a conta efetivamente autorizada, mesmo se for diferente do e-mail inicialmente informado.

E-mail sozinho não permite acessar o Drive. O JSON cadastra o aplicativo; a autorização do navegador concede acesso à conta. Não envie esse JSON nem seus tokens a este repositório.

No servidor remoto, use o painel por um [túnel SSH em localhost](deploy/README.md#adicionar-conta-google-no-servidor). Para login direto em um domínio, use um cliente OAuth web com HTTPS e retorno exato `https://seu-dominio/oauth/callback`.

O escopo atual é `https://www.googleapis.com/auth/drive`, necessário para navegar e sincronizar arquivos existentes. Projetos compartilhados para distribuição pública devem cumprir as [regras de escopos e verificação do Google](https://developers.google.com/workspace/drive/api/guides/api-specific-auth). Publicar o código não significa que um cliente OAuth coletivo já está aprovado.

## Criar e sincronizar um par

1. Selecione a conta Google.
2. Informe o nome do par e escolha uma pasta local existente.
3. Navegue até a pasta remota ou mantenha `root` para todo o Meu Drive.
4. Escolha a direção. Em uma primeira sincronização bidirecional, arquivos de mesmo caminho com conteúdos diferentes são sinalizados como conflito.
5. Salve o par e clique em **Sincronizar agora**.

No painel web, a pasta local pertence **ao servidor**. Uma pasta do computador do navegador precisa ser montada/compartilhada no servidor para ser usada. Não sincronize uma pasta com dados importantes sem manter uma cópia de segurança própria.

### Agendamento e sobrescrita

No painel web, escolha a frequência e quantidade. Por exemplo: **Por horas → Repetir a cada 6**. Escolha início após ligar o serviço, após o primeiro intervalo ou em um horário definido. O fuso usado pelo calendário é **America/Sao_Paulo (Brasília)**. Semanal permite escolher o dia da semana; mensal permite dia do mês e usa o último dia disponível em meses mais curtos. Execuções futuras por horário são preservadas entre reinícios. **Somente manual** desativa as execuções automáticas.

- **Comparar conteúdo:** copia arquivos alterados; no modo bidirecional, usa o histórico para determinar qual lado mudou e sinaliza alterações simultâneas.
- **Sobrescrever todos:** copia arquivos da origem mesmo quando idênticos; exige uma direção definida.
- **Comparar datas:** copia somente a versão mais recente; datas ausentes ou iguais com conteúdos diferentes geram conflito.

Exclusões não são propagadas. Arquivos ausentes podem ser restaurados pelo outro lado. Renomes são tratados como novos arquivos, mantendo o antigo.

### Relatórios por e-mail

No painel, abra **Relatórios por e-mail**, configure servidor, porta, TLS, usuário, remetente e senha de envio. Para Gmail, o botão **Usar servidor do Gmail** preenche os campos; use uma [senha de aplicativo](https://support.google.com/mail/answer/185833?hl=pt-BR), quando disponível na sua conta. O token do Drive não autoriza envio de e-mails.

Ative, salve e envie um teste. O destinatário de cada relatório é automaticamente o e-mail da conta Google do par, e não necessariamente o remetente SMTP. Mensagens são enviadas em segundo plano; se houver falha, há novas tentativas após 1, 5 e 15 minutos. Após quatro falhas, a fila e o log permanecem no servidor. O painel permite tentar novamente. Aceite SMTP não garante entrega na caixa de entrada.

O anexo contém o log completo daquela operação, em `.gz`, sem o limite dos últimos 1.000 registros mostrados no painel. Anexos compactados maiores que 20 MiB não são enviados; o relatório permanece salvo no servidor. Logs e fila não possuem limpeza automática nesta versão: acompanhe o uso do disco.

## Progresso e desempenho

A varredura do Drive e a leitura dos checksums locais ocorrem antes de calcular o total. Nessa etapa, a barra é indeterminada. Durante transferências, percentual e taxas usam bytes confirmados pela API; confirmações chegam por bloco, portanto a taxa pode oscilar e a barra pode ficar estável entre blocos. Isso não mede tráfego instantâneo da placa de rede.

Cada trabalhador reutiliza sua própria conexão HTTP. Arquivos distintos transferem em paralelo; cada arquivo grande usa uma conexão. Uploads são retomáveis durante a execução, mas não há retomada por blocos após fechar o processo. Downloads usam arquivo temporário, checksum e substituição após verificação.

Aumentar concorrência/blocos pode ajudar ou piorar o desempenho e aumenta memória. Não há garantia de atingir toda a conexão contratada. Varredura, armazenamento, CPU, latência e [limites da API do Google](https://developers.google.com/workspace/drive/api/guides/limits) influenciam o resultado.

## Dados locais e privacidade

Por padrão, os dados privados ficam **fora do projeto**, em `~/.config/drivebridge/`; `XDG_CONFIG_HOME` pode definir outro diretório:

| Arquivo/pasta | Conteúdo privado |
| --- | --- |
| `oauth-client.json`, `oauth-web-client.json` | Configuração do seu cliente OAuth |
| `accounts.json`, `tokens/` | Contas e tokens Google |
| `profiles.json`, `states/`, `schedule-state.json` | Pares, checkpoints e agenda |
| `web-settings.json`, `web-password.txt`, `web.env` | Configuração e senha do painel |
| `email-settings.json` | Configuração e senha SMTP |
| `activity.log*`, `reports/`, `email-outbox/` | Atividade, relatórios e fila de envio |

Arquivos de credenciais são protegidos por permissões de sistema (0600), **sem criptografia em repouso ou keyring**. Não compartilhe a pasta privada. Use HTTPS se o painel for exposto fora de um túnel/local confiável. Tokens são usados diretamente no Google; transferências não passam por um servidor do projeto. Relatórios vão ao serviço SMTP escolhido e ao destinatário configurado automaticamente.

Não adicione senhas, tokens, e-mails pessoais, logs reais ou capturas com dados de contas a issues e pull requests. Consulte [SECURITY.md](SECURITY.md).

## Limitações atuais

- Documentos nativos Google Docs/Sheets/Slides, atalhos, links simbólicos, nomes ambíguos/incompatíveis e caminhos inacessíveis são ignorados e registrados. Seus caminhos são reservados para evitar colisões.
- Não sincroniza pastas vazias ou unidades compartilhadas; não há exclusão espelhada, bandeja, keyring ou pacote RPM/Flatpak.
- Apenas uma sincronização ativa por instalação. Não inicie o desktop e o serviço web simultaneamente usando os mesmos tokens, pares e checkpoints.
- Falhas individuais ficam pendentes; falhas globais (sem espaço, autorização, rede persistente ou quota) interrompem a operação preservando o que terminou.
- O painel é para uso pessoal e não oferece isolamento de usuários. Não é um serviço público de múltiplos clientes.
- Para revogar uma conta, use as permissões de aplicativos na conta Google. A interface ainda não remove contas.

## Desenvolvimento e testes

```bash
.venv/bin/python -m pip install -e '.[web]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q drivebridge
node --check drivebridge/static/web.js  # opcional, se Node.js estiver instalado
```

Os testes usam arquivos temporários e mocks de Google/SMTP: não contêm uma conta real, não enviam e-mails reais e não medem velocidade real de rede. Cobrem conflitos, falhas, concorrência, progresso, autenticação, painel, calendário e relatórios. Veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

MIT. Veja [LICENSE](LICENSE). Este projeto é independente e não é afiliado ao Google, Insync ou rclone.
