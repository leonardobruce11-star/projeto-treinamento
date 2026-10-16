# Etapa 19.4-A — Consulta e autorização administrativa

Base: `feature/certificados-portal-etapa-193d`, commit `cab73c6`.
Branch: `feature/certificados-admin-etapa-194a`.

## Contratos

- `obter_certificado_emitido(certificado_id)` retorna um `ReciboCertificado`
  imutável ou `None` se o certificado não existe. IDs devem ser inteiros positivos
  de até 2147483647; outros valores geram `ErroCertificado('certificado_invalido')`.
  Erros SQLAlchemy são propagados ao chamador.
- A consulta usa uma sessão independente, sem autoflush ou commit, e seleciona
  diretamente `CertificadoTreinamento`. Não consulta participação, tokens, cadastros
  atuais ou elegibilidade. Não grava alterações pendentes do chamador.
- `recibo_de_certificado(certificado)` é a conversão pública que reutiliza `_recibo`.
  Aceita instâncias persistentes/desanexadas com atributos históricos carregados;
  rejeita objetos novos, de outro tipo ou removidos/marcados para remoção.
  O retorno sempre tem `criado=False`. Não valida elegibilidade nem corrige dados.
- Serviços não autorizam requisições HTTP: cada chamador deve aplicar autorização
  antes de expor os dados. A única rota adicionada usa o decorador `administrador`
  existente em `convites/routes.py`, sem duplicar regras de autenticação.

## Rota mínima

`GET /certificados/<int:certificado_id>` (também HEAD automático) retorna JSON com
ID do certificado, ID da participação, nome e título históricos, código e emissão
em ISO 8601. Mantém a representação temporal persistida, sem alterar datas.

Administrador ativo: 200. Não encontrado/ID fora do domínio: 404. Falha de banco:
503 com mensagem genérica. Operador autenticado: 403. Anônimo ou usuário inativo:
redirecionamento ao login pela factory real, cujo user_loader rejeita inativos.
A autorização antecede a consulta do certificado. Respostas do blueprint usam
`no-store`, `no-referrer` e `nosniff`. Não são registrados tokens ou códigos.

A consulta continua disponível para documentos ligados a cadastros inativos e
para certificados existentes cujo histórico da participação divergiu posteriormente.
O snapshot persistido não é atualizado ou revalidado. Esta etapa não atesta a
integridade do documento nem implementa reparo: análise de inconsistências nos
detalhes e validação para download pertencem às subetapas seguintes.

Não foram adicionados menu, templates, listagem, filtros, paginação, download,
migrações ou área pessoal. Emissão e fluxo do portal permanecem intactos.

## Testes

Virtualenv separado com o requirements do projeto; SQLite exclusivamente em
memória, ambiente de teste explícito e `load_dotenv` substituído por mock. A factory
real registra todos os blueprints e o user_loader real; Flask-Login e CSRF ficam
habilitados. Os usuários são fictícios, sem credenciais ONE.

A partir de `app/`, com o virtualenv de teste ativado:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python -m unittest \
  test_etapa194a_consulta_certificados \
  test_etapa193d_portal \
  test_etapa193c_pdf \
  test_portal_treinamento_quiz.QuizTest \
  test_portal_treinamento_progresso.ProgressoTest \
  test_convites_visao_geral -v
```

Resultado: 90 casos considerados, 84 aprovados e 6 pulados (variantes PostgreSQL
existentes de convites, banco descartável não configurado). Os 16 testes novos
passaram. Conferem autorização GET/HEAD antes da consulta, registros existentes e
ausentes, aprovação sem emissão, inatividade cadastral, snapshot, conversão pública,
IDs inválidos e falha de banco. Um snapshot de todas as tabelas e a captura de SQL
comprovam ausência de escrita nos GET/HEAD testados; mocks detectam chamadas ao
emissor e à validação de elegibilidade. Também verificam ausência de autoflush.

`test_etapa193b` foi invocada separadamente; a classe foi pulada por ausência de
PostgreSQL descartável local configurado.

## Pendências

Revisar o contrato JSON mínimo antes das interfaces das etapas B/C/D e validar
em PostgreSQL isolado na VM DEV. Os serviços exigem autorização do chamador também
em futuras rotas. Testes aprovados não constituem homologação. Nenhuma operação
em produção, migração, deploy ou reinício de serviço foi executado.
