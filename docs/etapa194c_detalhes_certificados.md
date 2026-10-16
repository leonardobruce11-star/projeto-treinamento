# Etapa 19.4-C — Detalhamento administrativo de certificados

Base: `feature/certificados-admin-etapa-194b`, commit `3c60e9c`.
Branch de trabalho: `feature/certificados-admin-etapa-194c`.

## Arquitetura e rotas

A nova rota `GET /certificados/<int:certificado_id>/detalhes` pertence ao blueprint
`certificados_admin` existente e também aceita HEAD automaticamente. Reutiliza
`obter_certificado_emitido`, sem duplicar consulta ou conversão para recibo.
O serviço existente lê diretamente `CertificadoTreinamento` em sessão independente,
sem commit/autoflush, e entrega um `ReciboCertificado` imutável.

O template `certificados_admin/detalhes.html` estende `base.html` e reutiliza os
componentes CSS de gestão e os cartões de informações já existentes. Exibe apenas
ID do certificado, nome do participante na emissão, título na emissão, código de
verificação e data/hora de emissão. Não consulta nem exibe tokens de participação,
credenciais, dados de campanhas ou outros dados cadastrais.

A data é apresentada como `dd/mm/aaaa HH:MM:SS UTC`, com fuso identificado. Datas
com timezone são convertidas apenas para apresentação em UTC; datas sem timezone
seguem a convenção UTC histórica já usada nos serviços. Nenhuma data é persistida
ou alterada durante a apresentação.

Rotas anteriores preservadas:

- `GET /certificados/`: listagem, filtros e paginação, com a mesma consulta.
- `GET /certificados/<int:certificado_id>`: contrato JSON da etapa A, sem alteração
  de campos, status ou representação temporal.
- Rotas existentes de certificados do portal permanecem intactas.

Não foram criadas rotas ou ações de PDF, emissão, edição, revogação ou exclusão.
Não há alterações na área pessoal ou no layout do certificado PDF.

## Autorização, privacidade e erros

A nova rota reutiliza `@administrador`, de `convites/routes.py`. A autorização
acontece antes de chamar o serviço de consulta:

- Administrador ativo: 200 para certificado existente.
- Anônimo: redirecionamento ao login.
- Operador: 403.
- Administrador inativo: redirecionamento ao login, conforme o user_loader real,
  que não restaura usuários desativados.
- Certificado inexistente ou ID fora do domínio aceito: 404. IDs de formato
  incompatível com o conversor inteiro da rota também retornam 404.
- Falha SQLAlchemy: 503, com mensagem genérica no template, sem exceção, SQL,
  parâmetros ou conteúdo do certificado na resposta.

Os cabeçalhos do blueprint permanecem `Cache-Control: no-store`,
`Referrer-Policy: no-referrer` e `X-Content-Type-Options: nosniff`. Os dados e links
são escapados automaticamente pelo Jinja2. Não foram adicionados logs de código,
tokens, dados históricos ou parâmetros de pesquisa.

A consulta continua disponível após desativação de colaborador/treinamento ou
mudança de nome/título atuais. O documento é apresentado a partir de seus próprios
snapshots, sem recálculo de aprovação, desempenho ou elegibilidade. Uma participação
aprovada sem certificado não resulta em emissão automática.

## Integração com a listagem

Foi adicionada uma coluna Ações, com o link **Visualizar detalhes** em cada linha.
Os parâmetros normalizados `participante`, `treinamento`, `codigo` e a página
atual acompanham o link. O botão **Voltar aos certificados** reconstrói o destino
com `url_for('certificados_admin.listar')`, preservando esse contexto.

A navegação de retorno aceita somente essas quatro chaves, com limites de tamanho
e descarte de valores repetidos, vazios, com controles ou página inválida. Contexto
inválido é ignorado, sem impedir a consulta do certificado. Parâmetros `next`, URLs
externas, tokens e outras chaves não são usados no retorno.

A listagem continua com 20 itens por página, filtros combinados, ordem por emissão
e ID decrescentes e os mesmos links de paginação. Não há link para a rota JSON ou
para funcionalidades ainda inexistentes. O menu já destaca o blueprint, portanto
não foi necessário modificar a navegação global ou o CSS.

## Arquivos criados

- `app/app/templates/certificados_admin/detalhes.html`
- `app/tests/test_etapa194c_detalhes_certificados.py`
- `docs/etapa194c_detalhes_certificados.md`

## Arquivos modificados

- `app/app/certificados_admin/routes.py`: rota de detalhes e contexto de retorno.
- `app/app/templates/certificados_admin/listar.html`: coluna e ação de detalhes.
- `app/tests/test_etapa194a_consulta_certificados.py`: a asserção das rotas admite
  a nova rota HTML, mantendo o bloqueio das rotas futuras.
- `app/tests/test_etapa194b_listagem_certificados.py`: verifica a presença do link
  de detalhes e continua impedindo link para JSON e PDF.

Modelos, serviços de consulta/emissão, gerador PDF, dependências e migrações não
foram modificados.

## Testes executados

Os testes novos reutilizam a factory real e as fixtures da etapa B, com Flask-Login
habilitado, SQLite exclusivamente em memória, ambiente fictício e `load_dotenv`
substituído por mock. Não carregam credenciais ou dados corporativos. O template
real é renderizado com `StrictUndefined`.

A partir de `app/`, com o virtualenv de testes ativado:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python -m unittest \
  test_etapa194c_detalhes_certificados \
  test_etapa194b_listagem_certificados \
  test_etapa194a_consulta_certificados \
  test_etapa193d_portal \
  test_etapa193c_pdf \
  test_portal_treinamento_quiz.QuizTest \
  test_portal_treinamento_progresso.ProgressoTest \
  test_admin_convites.AdminConvitesTest \
  test_convites_visao_geral.VisaoGeralTest \
  test_painel_colaboradores_pagina.PainelPaginaTest \
  test_painel_colaboradores_filtros.FiltrosTest \
  test_metricas_feedback.MetricasFeedbackTest -q
```

Resultado final: **187 aprovados, 0 falhos, 0 pulados**, incluindo 22 testes novos,
26 da etapa B e 16 da etapa A.

Cobertura nova: acesso por perfil, consulta autorizada antes dos dados,
inexistência/IDs inválidos, snapshots, cadastros inativos, código, data UTC com e
sem timezone, XSS, GET/HEAD sem escrita, ausência de emissão/revalidação, aprovação
sem documento, histórico divergente, navegação listagem → detalhes → listagem com
os três filtros e página preservados, retorno restrito ao destino interno, contrato
JSON, falha SQL controlada e uma única consulta ao certificado, sem relacionamentos.

A ausência de escrita é verificada por snapshot de todas as tabelas e captura de
SQL; mocks falham se emissão ou validação de elegibilidade forem chamadas.

## Decisões e pendências

- Foi adotado o sufixo `/detalhes` para manter a rota JSON sem conflito.
- A preservação do contexto de retorno é adicional à simples ligação entre as
  páginas, sem alterar as regras de pesquisa/paginação. Não houve desvio de escopo.
- Não há necessidade de migração ou nova dependência.
- Não havia PostgreSQL descartável configurado nesta execução; as variantes
  PostgreSQL não foram incluídas no comando. A validação anterior da etapa B
  informada na VM DEV não foi repetida nesta tarefa.
- Revisão visual em navegador e homologação funcional ficam para a revisão
  posterior autorizada. Testes automatizados aprovados não constituem homologação.
- O download administrativo permanece reservado à etapa 19.4-D. Revogação,
  exclusão, edição e emissão administrativa seguem fora do escopo.

Toda a execução ocorreu no ambiente isolado do Codex Cloud. Não houve acesso à
VM DEV `172.16.20.79`, à produção `172.16.20.189` ou a bancos corporativos. Não
houve migração, reinício de serviços, merge na main ou deploy.
