# Etapa 19.2C3 — Cardinalidade múltipla no banco, sem refação

## Estado e auditoria

Projeto base: `/opt/One_treino/app`. A implementação e os testes foram preparados em `/tmp/etapa192c3`, sem `.env` do DEV. O DEV foi consultado somente em transações PostgreSQL `READ ONLY`; nenhum arquivo operacional, worker ou registro foi alterado nesta etapa.

A auditoria confirmou o head `d192c1e4a909`, `numero_tentativa INTEGER NOT NULL DEFAULT 1`, a UNIQUE individual real `resultados_quiz_participacao_id_key`, a composta `uq_resultados_quiz_participacao_tentativa`, a PK, a FK `resultados_quiz_participacao_id_fkey` para `participacoes_treinamento(id)`, e os CHECKs `ck_resultados_quiz_numero_tentativa`, `ck_resultados_quiz_avaliacao_completa` e `ck_resultados_quiz_avaliacao_criterio`. O catálogo e as colunas estão em `logs/auditoria-dev.json`. A conferência final inclui FKs de entrada para a tabela de resultados e equivalência byte a byte dos arquivos operacionais com a baseline.

No código, `ResultadoQuiz.participacao` permanece escalar; `ParticipacaoTreinamento.resultados_quiz` permanece coleção ordenada por `(numero_tentativa, id)`, com `lazy="raise"`. Não há backref singular de compatibilidade. Os helpers puros da C2 e a subquery representativa permanecem com a mesma política: primeira aprovação por número crescente; sem aprovação, última tentativa classificada; sem classificados, último histórico. Não se escolhe a maior nota e `NULL` não é inferido como reprovação.

Consumidores auditados: portal do quiz e feedback; utilitários e rotas de painel/detalhe; dashboard e seus agregados; campanhas, decisão de piloto e templates; PDF; feedback, tempos e métricas de capacitação. Não foram encontrados novos acessos ao relacionamento singular em fontes ativas. `resultado_quiz_id` é uma coluna de projeção, e `resultado_quiz.html` é o nome do template. A busca está em `logs/consumidores.txt`.

## Modelo e migration

O único ajuste estrutural de modelo é remover `unique=True` de `ResultadoQuiz.participacao_id`. Preservam-se a FK, NOT NULL, todas as colunas, server default 1, UNIQUE composta, CHECKs, snapshots e relacionamento. O comentário dos helpers deixa claro que a alocação para refação pertence a um fluxo futuro; não foi implementado alocador nesta C3.

Nova revisão: **`e192c3e4a910`**, posterior a **`d192c1e4a909`**. Arquivo: `migrations/versions/e192c3e4a910_cardinalidade_resultados_quiz.py`.

O upgrade contém apenas `op.drop_constraint('resultados_quiz_participacao_id_key', 'resultados_quiz', type_='unique')`. Não há CASCADE, mudança de coluna, INSERT, UPDATE, DELETE, backfill ou recálculo. O PostgreSQL remove o índice que suporta essa UNIQUE individual; a UNIQUE composta já existente continua protegendo a identidade da tentativa e oferecendo índice com `participacao_id` como primeira coluna.

Os testes com a cadeia Alembic real comparam todas as colunas de resultados e todos os registros de resultados, participações e treinamentos antes/depois. Também comparam nome, OID, índice e definição das demais constraints: só a UNIQUE individual desaparece. A comparação **completa** de metadata Alembic, incluindo server defaults, não apresenta diferenças no head C3.

## Downgrade protegido

O downgrade toma `ACCESS EXCLUSIVE` nas tabelas `participacoes_treinamento` e `resultados_quiz`, nessa ordem, antes da verificação. A ordem acompanha o portal e o downgrade da C1. Os locks permanecem na transação Alembic até commit/rollback e cobrem a consulta `GROUP BY participacao_id HAVING COUNT(*) > 1` e a criação da UNIQUE.

Se houver qualquer participação com mais de uma linha, é lançado `RuntimeError` com mensagem explícita “Downgrade 19.2C3 bloqueado: existem participações com mais de um resultado de quiz…”. A falha desfaz a transação; os resultados, constraints e head C3 permanecem. Não se apagam ou renumeram tentativas para viabilizar rollback.

Sem multiplicidade, a UNIQUE é restaurada com o nome original. Uma participação com uma única linha numerada 7 pode voltar à C1, preservando o número 7; o downgrade adicional da C1 continua bloqueando a remoção da numeração diferente de 1. Essa distinção é testada. A UNIQUE recriada tem novo OID/índice, como esperado; preserva nome e semântica.

A migração exige execução online PostgreSQL para verificar dados no downgrade. Não se fornece downgrade offline que presuma ausência de multiplicidade. Os locks fortes também bloqueiam leituras; por isso se recomenda manutenção e drenagem de transações. Escritores SQL externos que tomem locks em ordem inversa podem causar deadlock: devem ser suspensos durante DDL. A migration não tenta cancelar sessões nem apagar dados automaticamente.

## Portal e idempotência

Nenhuma alteração de rota foi necessária: a C2 já fazia POST sob `FOR UPDATE` da participação, com `populate_existing=True`, seguido de `FOR SHARE` do treinamento. A conclusão é relida dentro dessa proteção; participação concluída retorna o resultado representativo e não insere avaliação. O lock é mantido até commit ou encerramento da sessão da requisição.

A criação continua com `numero_tentativa=1` fixo. Campos enviados pelo cliente não são usados. GET não cria linhas. Reenvios não reabrem participação, não substituem avaliação ou `aprovado_em`, e não modificam timestamps. A UNIQUE composta permanece como proteção adicional contra repetição de `(participacao_id, 1)`; a idempotência primária é o protocolo transacional, sem dependência da UNIQUE individual.

O teste de dois POSTs simultâneos verifica respostas 302/200, uma única tentativa 1 e snapshots estáveis no replay. Outro teste pausa o primeiro POST após obter o lock, confirma no servidor que o segundo aguarda em `wait_event_type='Lock'`, libera o primeiro e verifica 302/200, uma linha e `FOR SHARE` do treinamento nas duas conexões. A regressão anterior também exercita seis POSTs simultâneos. Não há botão de refação, elegibilidade de tentativa 2, alocador ou certificado.

## Persistência, consumidores e históricos

No PostgreSQL temporário foram verificados: uma, duas e cinco tentativas; default 1; rejeição de número repetido por participação, zero, negativo, NULL e FK inválida; históricos NULL inclusive valores legados fora das faixas classificadas; preservação de colunas/snapshots/datas; upgrade/downgrade/reupgrade; bloqueio de downgrade multi-linha; status e representativo Python × SQL no schema realmente migrado.

Os cenários C2 continuam cobrindo duas reprovações; reprovação seguida de aprovação; somente históricos; histórico/classificado/histórico; primeira aprovação seguida de nota maior, reprovação e histórico; treinamento sem quiz; métricas sem multiplicação e soma de tempo correta; painel, detalhe estruturado, dashboard/recentes/médias, campanhas/templates, PDF com uma linha por participação, feedback único e imutável, GET/replay sem nova linha e ausência de lazy load.

Os testes C2 agora criam múltiplas linhas naturalmente no schema de teste criado pelo modelo C3: foi removido o helper de `ALTER TABLE DROP CONSTRAINT`, sem reutilizar schema público ou banco operacional. Seu teste de duplicidade passa a verificar a repetição do número 1, enquanto os testes sintéticos continuam inserindo números distintos.

Na C1, os testes que migram especificamente para `d192c1e4a909` continuam verificando a UNIQUE individual real, sua identidade, preservação de histórico e rejeição da segunda linha. O teste de metadata na revisão C1 exige **exatamente uma diferença** em relação ao modelo C3: remoção dessa UNIQUE; não ignora diferenças arbitrárias. Os testes ORM de `create_all` representam o modelo atual e verificam a UNIQUE composta. As expectativas do head global em suites antigas foram atualizadas para C3; asserts de revisões históricas explícitas permanecem nos seus testes.

## Concorrência de downgrade

São exercitadas duas ordens, com conexões independentes, eventos SQL e confirmação de espera no servidor PostgreSQL:

1. Escritor toma o lock da participação e insere tentativa 2 antes de commitar. O downgrade tenta os locks e aguarda; após commit do escritor, enxerga a multiplicidade e falha. Duas linhas e head C3 permanecem.
2. Downgrade toma os locks e é pausado antes de verificar/restaurar. Um escritor que segue a ordem participação → resultado aguarda. Após restauração e commit, sua tentativa 2 é rejeitada pela UNIQUE individual; nenhum histórico é alterado e o head é C1.

Esses testes confirmam proteção entre verificação e DDL, sem sleeps usados como prova de exclusão. Há polling curto de `pg_stat_activity` para confirmar a espera efetiva.

## Performance e regressão

**13 testes específicos C3**, **47 combinados C1/C2/C3**, **1026 testes Python na suíte completa** e **9 testes JavaScript** aprovados, sem skips. A suíte completa inclui os testes específicos. Logs finais em `logs/c3.log`, `logs/especificos.log`, `logs/suite-python.log` e `logs/javascript.log`. Todos foram executados sobre as fontes finais em bancos isolados.

| Página real com templates | 1 tentativa | 5 tentativas |
|---|---:|---:|
| Painel | 5 | 5 |
| Dashboard | 29 | 29 |
| Detalhe | 8 | 8 |

O número de queries permanece constante. O volume de registros processados/carregados cresce com o histórico; estes testes não são benchmark de tempo, memória ou volume operacional massivo. O dashboard mantém seu fator preexistente de consultas por departamento.

PostgreSQL 16 descartável, UTF-8, socket `/tmp/etapa192c3/pgsocket`, porta 55495 e listener TCP desabilitado. As fixtures usam schemas exclusivos com UUID e fazem cleanup. `fsync` e `synchronous_commit` foram desativados somente nesse cluster descartável para acelerar DDL; não se testou recuperação de falha física. SMTP e GoPhish usam os doubles/mocks da suíte; nenhuma ação real ou conexão externa foi realizada. Nenhum downgrade foi executado no DEV.

Para reprodução, em cópia completa do projeto sem `.env` operacional, apontar `ONE_PROGRESSO_TEST_DATABASE_URL`, `ONE_HEARTBEAT_TEST_DATABASE_URL` e `ONE_APROVACAO_MIGRATION_TEST_DATABASE_URL` exclusivamente ao PostgreSQL descartável e executar:

```sh
python -B -m unittest discover -s tests -p test_etapa192c3.py -v
python -B -m unittest discover -s tests -p 'test_etapa192c*.py' -v
python -B -m unittest discover -s tests -v
node --test tests/test_portal_treinamento_heartbeat_browser.cjs
```

## Aplicação futura no DEV — procedimento preparado, não executado

A C2 aplicada é compatível tanto com a UNIQUE individual presente quanto com sua ausência; seus consumidores já leem N resultados e seu portal continua na tentativa 1. O patch C3 também funciona antes do upgrade, pois remover `unique=True` do mapeamento não remove a constraint real por si só. Mesmo assim, recomenda-se uma janela curta para evitar templates/código misturados e espera de DDL.

1. Obter aprovação de aplicação desta entrega. Conferir checksum do patch, hashes da base no manifesto, head C1 e catálogo real. Não aplicar automaticamente sobre arquivos divergentes. Preparar cópia dos arquivos afetados e backup PostgreSQL pelo procedimento autorizado do ambiente, com credenciais protegidas.
2. Ativar manutenção no proxy/entrada do DEV; suspender escritores e scripts de administração que possam tocar participações/resultados. Drenar requisições e parar `one-treinamentos.service` de forma controlada. A pausa deve cobrir todos os escritores, inclusive sessões externas ao Gunicorn.
3. Inspecionar transações em andamento no banco DEV (`pg_stat_activity`: PID, estado, início e duração), sobretudo `idle in transaction`. Aguardar fechamento. Não terminar sessões automaticamente. A remoção de UNIQUE exige `ACCESS EXCLUSIVE` em resultados e pode esperar por leitores que conservam transações abertas.
4. Conferir o patch com `patch --binary --dry-run -p1` no diretório do projeto e aplicá-lo somente após a conferência. Nenhum arquivo `.env` faz parte do patch. Não executar autogenerate ou `db.create_all` no DEV.
5. No diretório do projeto, com a configuração DEV habitual e ambiente conferido, executar a revisão explícita `e192c3e4a910`. Recomenda-se limitar espera por lock e tempo de comando, por exemplo:

```sh
PGOPTIONS='-c lock_timeout=5s -c statement_timeout=60s' \
  .venv/bin/flask --app run:app db upgrade e192c3e4a910
```

Se houver timeout, dependência inesperada ou erro de constraint, manter manutenção e investigar. O DDL PostgreSQL e o versionamento Alembic estão na mesma transação; não fazer `stamp` para forçar o head nem executar DROP manual em outra conexão.

6. Confirmar head C3, ausência **somente** da UNIQUE individual e presença da composta, FK, PK, CHECKs, NOT NULL e default. Comparar contagens/históricos antes/depois. Confirmar que o portal instalado ainda fixa o número 1. Não inserir tentativa 2 no DEV como smoke test.
7. Iniciar/reiniciar `one-treinamentos.service`, verificar os dois workers, logs de inicialização e login. Validar páginas de leitura com conta de teste autorizada: painel, detalhe, dashboard, campanha/PDF e feedback existente quando houver dados. Reabrir entrada e escritores apenas depois da verificação.

Se o upgrade falhar sem mudar o schema, os workers C2 podem voltar com os arquivos de origem após conferência. Se o upgrade tiver sido concluído e for necessário rollback, manter manutenção/drenagem e só executar o downgrade para `d192c1e4a909` após aprovação específica. Ele próprio verifica multiplicidade sob locks. Se bloquear, manter o schema C3 e consumidores C2/C3 compatíveis; não apagar resultados. Não executar downgrade para revisões anteriores em cascata sem avaliar suas proteções próprias.

Esse procedimento não foi aplicado. Não foram alterados serviço/proxy, schema ou registros do DEV.

## Riscos residuais e limites

O banco passa a aceitar números distintos, mas não implementa sequência monotônica sem lacunas nem autoriza refação. Escritores SQL externos podem inserir novas linhas numeradas; devem ser controlados operacionalmente. A futura escrita de refação precisa de elegibilidade, identidade idempotente de envio, alocação sob lock, imutabilidade de snapshots e preservação da primeira aprovação. Nenhum desses recursos foi iniciado aqui.

Uma vez existente multiplicidade, rollback para a UNIQUE individual deixa de ser possível sem uma decisão explícita sobre dados; esta migration deliberadamente não toma essa decisão. Locks exclusivos podem causar espera ou indisponibilidade e são motivo para manutenção. Ordem inversa de locks em SQL externo ou isolamento diferente do READ COMMITTED padrão precisa de revalidação. Históricos inconsistentes continuam exibidos sem reparo retroativo; esta etapa não reconcilia timestamps/nota/aprovação.

O downgrade contém verificação online e não pode ser emitido como SQL offline presumindo que o banco tem uma linha por participação. O upgrade pode falhar por dependências inesperadas; não usa CASCADE para contorná-las. Não há certificado, botão de refação ou liberação da tentativa 2 no portal, nem início da próxima etapa.

## Entrega e integridade

Artefatos solicitados: `/home/oneti/etapa192c3/etapa192c3.patch`, `RELATORIO.md` e `manifesto.json`, com fontes e logs complementares. O manifesto registra hashes da base e das fontes finais, revisão de origem/destino, restrições e resultados. O patch é validado com dry-run e aplicação somente em cópia descartável; os arquivos resultantes devem corresponder byte a byte às fontes testadas. A conferência final confirma que o DEV mantém seus arquivos, head C1 e UNIQUE antiga.
