# Etapa 19.2C1 — Fundação de persistência das tentativas

Implementação preparada em `/tmp/etapa192c1`, a partir de `/opt/One_treino/app`. Nenhum arquivo ou dado do DEV foi alterado. Produção não foi acessada. Sem refação, certificados ou início da 19.2C2. Entrega em `/home/oneti/etapa192c1/`.

## Auditoria da base

Head do DEV confirmado em transação PostgreSQL local `READ ONLY`: `c199b1e4a908`. A constraint real de unicidade é `resultados_quiz_participacao_id_key`, `UNIQUE (participacao_id)`. Mantêm-se `resultados_quiz_pkey`, a FK da participação e os CHECKs `ck_resultados_quiz_avaliacao_completa` e `ck_resultados_quiz_avaliacao_criterio`. Evidência: `logs/auditoria-dev.json`, sem dados pessoais ou credenciais.

`ResultadoQuiz.participacao` continua com backref `ParticipacaoTreinamento.resultado_quiz`, `uselist=False`. `participacao_id` continua `nullable=False, unique=True`. O portal consulta o resultado com `scalar_one_or_none()` e não cria outra linha para participação concluída ou replay. A única escrita aplicativo de ResultadoQuiz encontrada é o POST do quiz em `app/portal_treinamento/routes.py`; as outras construções/INSERTs são fixtures de testes. Campanhas, dashboard, colaboradores, painel, relatório PDF e feedback consomem a leitura singular. Backups `.orig`/`.bak` não são caminhos ativos.

## Modelo e comportamento incremental

Foi acrescentado `numero_tentativa INTEGER NOT NULL DEFAULT 1`, com:

- `ck_resultados_quiz_numero_tentativa`: `CHECK (numero_tentativa >= 1)`;
- `uq_resultados_quiz_participacao_tentativa`: `UNIQUE (participacao_id, numero_tentativa)`;
- a UNIQUE antiga por `participacao_id` permanece, com o mesmo nome, OID e índice no ensaio de migration.

O default é **no servidor**, preservado na migration e no metadata (`server_default=db.text("1")`). INSERTs SQL e ORM que omitam a coluna continuam criando tentativa 1; SQL com NULL explícito é rejeitado pelo NOT NULL. A leitura ORM recebe o default após o INSERT. Não é necessário um default exclusivamente Python, que não atenderia SQL direto.

O portal fixa explicitamente `numero_tentativa=1`; não lê esse valor do cliente nem possui contador, alocador de sequência ou ação de refação. Um POST com `numero_tentativa=2` ou `999` continua gravando apenas 1 e reenvio mantém ID, notas, classificação e datas. A UNIQUE antiga impede uma segunda linha para a mesma participação, mesmo com outro número. Conclusão sem quiz permanece sem ResultadoQuiz.

A coluna aceita inteiros positivos maiores que 1 para sua evolução futura; SQL administrativo pode alterar a numeração de uma única linha, mas isso não libera duas linhas por participação. Não há mudança de elegibilidade, relações, queries dos consumidores ou regra de correção/aprovação.

## Migration e preservação histórica

Revisão nova: `d192c1e4a909_numero_tentativa_quiz.py`.

Cadeia: `c199b1e4a908 → d192c1e4a909`. Head único esperado **após futura aplicação autorizada**: `d192c1e4a909`. O DEV permanece em `c199b1e4a908` nesta entrega; leitura final confirmou também que `numero_tentativa` ainda não existe nele (`logs/verificacao-dev.json`).

O upgrade adiciona a coluna com default constante 1 e NOT NULL e cria somente o novo CHECK e a UNIQUE composta. PostgreSQL atribui 1 também às linhas existentes. Não há UPDATE de percentuais, acertos, total de questões, critérios, aprovado, IDs, participação, início, conclusão ou primeira aprovação. Em PostgreSQL 16 o default constante permite a otimização de ADD COLUMN sem recalcular registros; validação de CHECK e criação da UNIQUE ainda podem percorrer a tabela.

O downgrade obtém `ACCESS EXCLUSIVE` em participação → resultados, seguindo a ordem das migrations anteriores e dos caminhos de escrita do portal. Só depois consulta se há `numero_tentativa <> 1`. Se houver, lança erro controlado antes de remover qualquer objeto; a transação conserva dados, coluna, constraints e revisão. Se todos forem 1, remove apenas a UNIQUE composta, o CHECK novo e a coluna. As constraints e os campos da 19.1 permanecem. Locks persistem até commit/rollback e impedem escrita entre a verificação e o DROP.

Os testes não removem a UNIQUE antiga nem para ensaiar a composta: validam suas colunas/metadata e exercitam `ON CONFLICT ON CONSTRAINT uq_resultados_quiz_participacao_tentativa DO NOTHING`. Uma tentativa de segunda linha com outro número é rejeitada especificamente pela UNIQUE antiga. O downgrade protegido é ensaiado alterando uma linha sintética para número 2, sem liberar refação.

## Arquivos do patch

1. `app/models/resultado_quiz.py`: coluna, default e duas constraints adicionais; relacionamento singular intacto.
2. `app/portal_treinamento/routes.py`: apenas `numero_tentativa=1` na escrita atual.
3. `migrations/versions/d192c1e4a909_numero_tentativa_quiz.py`: upgrade e downgrade protegidos.
4. `tests/test_etapa192c1.py`: testes novos funcionais SQLite/PostgreSQL e de migration PostgreSQL.
5. `tests/test_aprovacao_migration.py`: expectativa do novo head único.
6. `tests/test_feedback_treinamento.py`: expectativa do novo head único.
7. `tests/test_persistencia_aprovacao_migration.py`: novo head e verificação de que as uniques anteriores permanecem após upgrade; downgrade continua exigindo a configuração antiga exata.
8. `docs/etapa-19.2C1-fundacao-tentativas.md`: relatório técnico.

Migrations anteriores não foram reescritas. Não há alteração de templates, campanhas, relatórios, painel, métricas, convites, feedback, simulações ou congelamento 19.2B. As expectativas dos testes antigos acompanham o novo head sem remover suas verificações de preservação e downgrade.

## Validação

Resultados finais: **14 testes específicos**, **993 testes na suíte Python completa** e **9 testes JavaScript aprovados, sem skips**. A suíte completa inclui os testes específicos e os de concorrência da 19.2B. Evidências em `logs/especificos.log`, `logs/suite-python.log` e `logs/javascript.log`. Sem regressões identificadas em criação de treinamento, módulos, quiz, aprovação 19.1, conclusão sem quiz, feedback, painel/métricas, campanhas, convites ou simulações.

Os seis testes de migration ensaiam upgrade/downgrade/reupgrade em schema exclusivo, dados não classificados inclusive valores históricos fora das faixas atuais, aprovação True/False com critérios, timestamps distintos, SQL legado sem número, NULL, zero, negativos, overflow de INTEGER, número máximo válido, uniques, comparação de metadata com `compare_server_default=True` e rollback protegido. São comparados todos os campos anteriores das linhas de resultado e todas as participações; a constraint e o índice da UNIQUE antiga preservam seus OIDs.

A concorrência real pausa uma escrita que mudou uma linha sintética para número 2 e confirma que o downgrade aguarda seu commit; após vê-la, bloqueia o DROP. Não é simulação de lock SQLite e não implementa numeração concorrente de novas tentativas.

Os oito testes funcionais (quatro em cada banco) confirmam default ORM, backref singular, recusa da segunda linha, replay idempotente, número fornecido pelo cliente ignorado e conclusão sem quiz.

PostgreSQL 16 temporário UTF-8, socket Unix exclusivo em `/tmp/etapa192c1/pgsocket`, sem listener TCP. `fsync` e `synchronous_commit` desativados apenas no cluster descartável para reduzir DDL; locks e transações são reais. Não é ensaio de recuperação física de crash. Todos os URLs de teste apontam ao banco temporário, não ao DEV. Nenhum SMTP ou GoPhish real foi acionado.

Para reproduzir, usar cópia completa do projeto sem `.env` do DEV, PostgreSQL descartável inicializado com `--encoding=UTF8` e diretório `/tmp/one-18.7E-review` esperado pelos testes de logs existentes. Definir `ONE_PROGRESSO_TEST_DATABASE_URL`, `ONE_HEARTBEAT_TEST_DATABASE_URL` e `ONE_APROVACAO_MIGRATION_TEST_DATABASE_URL` para o banco local. Executar `python -m unittest discover -s tests -p test_etapa192c1.py -v`, `python -m unittest discover -s tests -v` e `node --test tests/test_portal_treinamento_heartbeat_browser.cjs`.

## Geração concorrente futura — apenas desenho

Depois de adaptar os consumidores e retirar a UNIQUE individual em uma subetapa autorizada, toda nova tentativa deverá começar pela participação `FOR UPDATE`, em uma mesma transação até gravar resultado, conclusão/primeira aprovação e commit. Sob esse lock, revalidar acesso/elegibilidade e identidade idempotente de envio, e só então ler `MAX(numero_tentativa)` daquela participação e atribuir `COALESCE(MAX,0)+1`. Não calcular o próximo número antes de adquirir o lock; o número enviado pelo cliente não é autorização para criá-lo.

Todos os escritores precisam seguir o mesmo protocolo. Dois POSTs da mesma participação serão serializados; após esperar, o segundo deve distinguir replay da mesma submissão de tentativa efetivamente nova. A UNIQUE composta será a proteção adicional de integridade; sozinha não substitui lock nem identidade de envio. Replay deve retornar o resultado já persistido, sem reservar/consumir número ou corrigir novamente. Falha/rollback não deve deixar tentativa parcial nem mudar primeira aprovação.

Manter participação `FOR UPDATE` → treinamento `FOR SHARE` para snapshot do quiz e da nota mínima, compatível com a proteção exclusiva da 19.2B. Não criar linhas por GET, não reiniciar cursor/conclusão e não apagar aprovação anterior. Esse alocador, identidade de envio e política de refação **não foram implementados** na 19.2C1.

## Consumidores obrigatórios antes de retirar a UNIQUE antiga

| Consumidor | Migração necessária antes de aceitar múltiplas linhas |
|---|---|
| Modelo ResultadoQuiz/ParticipacaoTreinamento | Evoluir juntos backref singular e cardinalidade; escolher coleção e consultas explícitas, sem propriedade que cause N+1. |
| Portal `quiz` e resultado/conclusão | Substituir `scalar_one_or_none`, distinguir resultado representativo, histórico, replay e elegibilidade; conservar primeira conclusão/aprovação e snapshot. |
| Feedback do portal | Trocar a consulta/presença singular do link de retorno; preservar feedback único por participação. |
| `utils/painel_colaboradores.py` | Resumo com uma linha por participação antes de JOINs, contagens, somas de tempo e médias; selecionar resultado conforme política explícita. |
| Colaboradores: painel, detalhe e templates | Evoluir selectinload/backref singular, situação, notas, critérios e histórico; preservar orçamento de queries. |
| Dashboard | Separar contagem/média por participação das métricas de tentativas; adaptar resultados recentes e consultas por participação. |
| Campanhas: detalhe, relatórios, análise/validação piloto | Adaptar listas singularizadas, médias/status, eager loads e `.first()` sem ordem; não alterar silenciosamente a política administrativa do piloto. |
| `relatorios/campanha_pdf.py` e templates de campanha | Usar o mesmo resumo e critério de resultado representativo das telas. |
| Convites, métricas e simulações | Auditar semântica indireta: conclusão não deve ser reaberta; tempo e participação não podem multiplicar com JOINs; importação não sobrescreve histórico. |
| Fixtures, testes e SQL administrativo | Evoluir `.one()`, unicidade antiga, SELECT/INSERTs e replay; validar status Python/SQL, legado, feedback, métricas, concorrência e desempenho. |

A política de nota representativa (última, melhor, primeira aprovada etc.) precisa ser decidida e aplicada de forma central; não assumir que a maior nota identifica a primeira aprovação. C2 e refação não foram iniciadas nesta entrega.

## Aplicação futura e riscos

O patch contém modelo novo que seleciona a coluna nova. Portanto, após autorização, aplicar a migration ao schema do DEV **antes** de recarregar workers com o modelo novo. O schema aditivo é compatível com workers antigos: INSERTs que omitem o número recebem 1. Subir o modelo novo contra o head antigo causaria coluna inexistente. O patch não foi aplicado e o serviço DEV não foi reiniciado nesta etapa.

As constraints adicionais exigem locks de DDL e construção/validação de índice, podendo aguardar transações existentes e bloquear writes durante a aplicação; prever janela controlada e verificar a ausência de transações longas. A ordem de locks da migration segue o portal; escritores SQL administrativos com outra ordem devem ser coordenados, especialmente em downgrades. Não houve ensaio com volume real do DEV nem promessa de tempo de execução em produção.

Downgrade da C1 é permitido apenas se todos os números forem 1. Downgrades mais antigos continuam sujeitos às proteções da 19.1, que impedem perder aprovação/classificação. Não reclassificar dados para contornar essas proteções.

A UNIQUE individual limita uma linha até a adaptação completa dos consumidores; **não remover nesta subetapa**, apesar de redundante em conjunto com a composta. A numeração futura também precisará decidir limite/overflow de INTEGER e política de retenção, sem limpar/reutilizar números históricos. Nenhum certificado foi criado.
