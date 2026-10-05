# DriveBridge

O projeto foi separado em duas distribuições independentes, com instaladores e documentação em português:

| Projeto | Uso | Instalação |
| --- | --- | --- |
| [DriveBridge-Desktop](https://github.com/poajefersonld-1983/DriveBridge-Desktop) | Aplicativo de janela para Linux, com atalho e agendamento por intervalo | Debian/Ubuntu e Fedora |
| [DriveBridge-Web](https://github.com/poajefersonld-1983/DriveBridge-Web) | Serviço pessoal no Debian, acessado pelo navegador, com calendário e relatórios por e-mail | Debian/Ubuntu |

## Instalar o aplicativo desktop

```bash
curl -fsSL https://raw.githubusercontent.com/poajefersonld-1983/DriveBridge-Desktop/main/install.sh -o drivebridge-desktop-install.sh && bash drivebridge-desktop-install.sh
```

## Instalar o serviço web

```bash
curl -fsSL https://raw.githubusercontent.com/poajefersonld-1983/DriveBridge-Web/main/install.sh -o drivebridge-web-install.sh && bash drivebridge-web-install.sh
```

Cada instalação conecta a própria conta Google. Nenhum repositório inclui contas, tokens, clientes OAuth ou senhas pessoais. Consulte o README do projeto escolhido para configuração do Google e requisitos.

Este repositório é agora apenas o índice e a documentação da separação. A versão inicial conjunta permanece no histórico Git; os desenvolvimentos seguintes seguem nos dois repositórios acima. O instalador antigo continua aceitando `--desktop` e `--web` e encaminha para a distribuição correspondente.

## Instalações existentes

A separação publicada não atualiza nem reinicia instalações existentes. As novas versões usam pastas de projeto e de configuração distintas: `~/DriveBridge-Desktop` e `~/.config/drivebridge-desktop/`; `~/DriveBridge-Web` e `~/.config/drivebridge-web/`. O novo serviço se chama `drivebridge-web.service`. Contas antigas não são importadas automaticamente. Para migrar seus próprios dados, faça backup privado e consulte a documentação do projeto escolhido. Não sincronize os mesmos pares simultaneamente nas instalações antiga e nova. Se houver outro serviço usando a porta 8768, pare esse serviço ou configure outra porta antes de iniciar a nova versão web.

## Licença

[MIT](LICENSE). Código independente, usando diretamente a API do Google, sem rclone.
