# Segurança e dados privados

Não publique contas Google, tokens, JSON OAuth, senhas SMTP, senhas do painel, arquivos `web.env`, logs de uso ou capturas com dados pessoais.

Dados da instalação ficam em `$XDG_CONFIG_HOME/drivebridge` (normalmente `~/.config/drivebridge`), fora do projeto. As credenciais usam permissões 0600, sem criptografia em repouso. Proteja o usuário do sistema e os backups.

O painel usa senha, sessão e CSRF, mas é para uma instalação pessoal: não há isolamento de usuários. A pessoa com acesso pode operar todos os pares e navegar pelas pastas acessíveis ao serviço. Prefira localhost + túnel SSH; use HTTPS para acesso por domínio. Não exponha a instância pessoal diretamente à internet.

Para relatar um problema de segurança, não abra uma issue pública com credenciais nem dados reais. Use o aviso privado do GitHub, quando disponível, ou uma reprodução sanitizada. Se uma credencial for publicada, revogue-a no provedor; apagar o arquivo ou commit não basta.

Antes de publicar uma alteração, rode `python tools/audit_publication.py`. Essa checagem ajuda a detectar arquivos indevidos e padrões de credenciais, mas não substitui a revisão humana dos arquivos e do histórico Git.
