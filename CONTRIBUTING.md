# Contribuir

Contribuições são bem-vindas. Descreva o problema, a mudança e como foi validada. Não inclua dados da sua conta nem logs reais sem sanitizá-los.

1. Faça um fork e clone seu fork.
2. Crie uma branch para a alteração.
3. Instale `.[web]` em um ambiente virtual.
4. Execute os testes com `python -m unittest discover -s tests -v`.
5. Verifique os arquivos públicos com `python tools/audit_publication.py`.
6. Abra um pull request com resumo e resultados dos testes.

Testes usam diretórios temporários e mocks. Não faça testes de integração automáticos com contas reais, credenciais ou envios SMTP reais no CI. Para mudanças na interface, verifique desktop e/ou web conforme o recurso alterado.

Preserve cancelamento, integridade dos arquivos, conflitos e isolamento das conexões HTTP por trabalhador. Não sacrifique verificação de dados para aumentar uma taxa exibida. Novas dependências devem ter uma necessidade clara.

Áreas úteis: reduzir varreduras repetidas com segurança, cache de hashes validado, testes reais de desempenho controlados, acessibilidade, empacotamento Linux e proteção de credenciais.
