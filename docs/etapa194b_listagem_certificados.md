# Etapa 19.4-B — Listagem administrativa de certificados

Base obrigatória: `feature/certificados-admin-etapa-194a`, commit `5f69c91`.
Branch de trabalho: `feature/certificados-admin-etapa-194b`.

## Arquitetura

A rota de listagem utiliza o blueprint `certificados_admin` já registrado na
factory. O decorador `administrador`, de `convites/routes.py`, autoriza a requisição
antes da validação dos parâmetros e da consulta de dados.

O serviço `listar_certificados_emitidos` foi adicionado a
`services/consulta_certificados.py`. Ele consulta diretamente
`CertificadoTreinamento`, usando uma sessão SQLAlchemy independente com
`autoflush=False`, sem commit, sem emissão e sem consultar aprovação, participação
ou cadastros atuais. Realiza duas consultas: contagem filtrada e seleção ordenada
com LIMIT/OFFSET. No máximo 20 certificados são materializados por chamada;
não há junções, carregamento de relacionamentos ou consultas N+1.

O retorno `PaginaCertificados` é imutável, com itens em tupla de
`ReciboCertificado`, filtros normalizados, página atual, quantidade de páginas e
total filtrado. A conversão pública da etapa A é reutilizada. A consulta por ID e
seu retorno JSON continuam disponíveis sem alteração do contrato anterior.

O template estende `base.html` e reutiliza os componentes de gestão, formulário,
tabela e botões do CSS existente. A tabela apresenta ID, nome histórico, título
histórico, código e data de emissão em UTC. Não substitui snapshots por dados
cadastrais atuais. Certificados de cadastros inativos também aparecem.

## Rotas e parâmetros GET

Nova rota: `GET /certificados/`, com HEAD automático.

| Parâmetro | Regra |
| --- | --- |
| `participante` | Trecho do nome persistido, até 150 caracteres. |
| `treinamento` | Trecho do título persistido, até 200 caracteres. |
| `codigo` | Trecho do código de verificação, até 64 caracteres. |
| `page` | Inteiro ASCII entre 1 e 2147483647; padrão 1. |

Filtros textuais são opcionais, têm espaços nas extremidades removidos e são
combinados por AND. As buscas parciais usam ILIKE parametrizado; `%`, `_` e `!`
são escapados para pesquisa literal, seguindo o padrão existente do painel.
Não se implementou remoção de acentos. Limites de tamanho são aplicados antes da
consulta, também no serviço, independentemente do maxlength do formulário.

Caracteres de controle, filtros longos, parâmetros conhecidos repetidos e páginas
vazias, não numéricas, negativas, zero ou fora do domínio retornam 400. Parâmetros
desconhecidos são ignorados; não controlam ordenação ou tamanho da página.

A página contém sempre no máximo 20 registros. A ordenação é
`emitido_em DESC, id DESC`. Página numérica além do conjunto é limitada à última;
no conjunto vazio, página 1 de 1. A contagem considera os mesmos filtros da
seleção. Os links Anterior/Próxima preservam todos os filtros normalizados.
O formulário inicia uma nova pesquisa na página 1 e oferece Limpar filtros.

Mantida: `GET /certificados/<int:certificado_id>`, consulta JSON da etapa A.
A listagem não inclui links para detalhes ou PDF.

## Autorização e segurança

- Administrador ativo: acesso permitido.
- Operador autenticado: 403, sem consulta dos certificados.
- Anônimo ou administrador inativo: redirecionamento ao login, conforme o
  user_loader real da aplicação, sem consulta dos certificados.
- Link Certificados na seção Capacitação, visível somente para administrador
  ativo. O destino é a listagem e o item fica ativo no blueprint correspondente.
- Cabeçalhos existentes do blueprint preservados: `Cache-Control: no-store`,
  `Referrer-Policy: no-referrer` e `X-Content-Type-Options: nosniff`.
- Dados e filtros exibidos com escape automático do Jinja2. Tokens do portal não
  são consultados nem usados nas URLs administrativas.
- Falha de banco retorna uma página com mensagem genérica e status 503, sem
  detalhes SQL ou parâmetros privados.
- GET/HEAD não emitem, não validam elegibilidade e não alteram snapshots, notas,
  datas, resultados, submissões ou quaisquer outros registros.
- Os serviços continuam internos: futuros chamadores precisam aplicar autorização
  HTTP antes de expor resultados.

Não houve nova dependência, migração ou modificação em campanhas, phishing, quiz,
emissão ou autenticação. Nenhuma interface pessoal foi criada.

## Arquivos criados

- `app/app/templates/certificados_admin/listar.html`
- `app/tests/test_etapa194b_listagem_certificados.py`
- `docs/etapa194b_listagem_certificados.md`

## Arquivos modificados

- `app/app/services/consulta_certificados.py`: filtros, contagem e página limitada.
- `app/app/certificados_admin/routes.py`: rota GET de listagem.
- `app/app/templates/base.html`: menu condicionado ao perfil e estado ativo.
- `app/tests/test_etapa194a_consulta_certificados.py`: atualização da asserção de
  rotas permitidas para admitir a nova listagem, mantendo o bloqueio das rotas
  futuras. As verificações anteriores de consulta e autorização permanecem.
- `app/tests/test_admin_convites.py`: inclusão do destino Certificados na fixture
  que simula os destinos do menu compartilhado; sem alteração do fluxo testado.

## Testes executados

Virtualenv separado, com dependências do requirements do projeto. Os novos testes
reutilizam a factory real da etapa A, com Flask-Login habilitado, ambiente fictício
explícito, SQLite em memória e `load_dotenv` substituído por mock. Nenhuma
credencial ONE é carregada. As demais suítes selecionadas usam suas fixtures
isoladas existentes.

A partir de `app/`, com o virtualenv de testes ativado:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python -m unittest \
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

Resultado final: **165 aprovados, 0 falhos, 0 pulados**. Inclui 26 testes novos da
listagem e os 16 testes de regressão da etapa A.

A cobertura inclui acesso administrativo, bloqueios GET/HEAD antes da consulta,
estado vazio, snapshots após mudanças cadastrais, cadastros inativos, aprovação
sem certificado, os três filtros e suas combinações, curingas e tentativa de
injeção, escape HTML, limites e parâmetros repetidos, paginação em três páginas,
ordem determinística por data/ID, filtros nos links, menu por perfil, erro de banco
e ausência de rotas futuras. SQL capturado comprova consultas com LIMIT e sem
N+1; snapshots de todas as tabelas e mocks dos emissores/validação verificam que
GET/HEAD não escrevem nem emitem. Também é verificada ausência de autoflush do
chamador.

## Decisões, limitações e pendências

- Foram escolhidos três filtros separados combináveis, em vez de um campo de
  busca global. Não houve desvio de escopo funcional.
- Nenhuma migração é necessária. Pesquisas parciais podem demandar índices em
  volumes maiores; avaliar com plano de execução e dados representativos em
  ambiente isolado antes de propor alteração de schema.
- Paginação por OFFSET não congela o conjunto entre requisições: novas emissões
  concorrentes podem deslocar páginas. Contagem e seleção também não constituem
  um snapshot imutável sob o isolamento padrão do PostgreSQL.
- Nesta execução não havia PostgreSQL descartável configurado; as variantes
  PostgreSQL não foram incluídas no comando acima. Validar ILIKE, collation,
  ordenação e desempenho em PostgreSQL isolado na revisão posterior.
- Revisão visual em navegador e homologação funcional na VM DEV ficam para o
  responsável autorizado. Testes aprovados não constituem homologação.
- A busca por código mantém esse filtro na URL, conforme o requisito; a política
  operacional de logs deve proteger esses parâmetros. Não foram adicionados logs
  de códigos, filtros ou tokens.
- Detalhes visuais, download administrativo, emissão administrativa, revogação,
  exclusão e área pessoal continuam fora desta etapa.

Toda a execução ocorreu no repositório e no ambiente isolado do Codex Cloud.
Não houve acesso à VM DEV `172.16.20.79`, à produção `172.16.20.189` ou a bancos
corporativos. Não houve migração, reinício de serviço, merge na main ou deploy.
