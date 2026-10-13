# Etapa 19.2B — Congelamento estrutural do quiz

Implementação em cópia isolada de `/opt/One_treino/app`, em `/tmp/etapa192b`. Nenhuma aplicação no DEV, nenhuma conexão ao banco DEV, nenhuma conexão externa. Sem múltiplas tentativas, certificados ou início da 19.2C. Os artefatos anteriores em `/home/oneti/etapa192b` foram preservados; esta entrega será armazenada em `/home/oneti/etapa192b-20261008`.

## 1. Brechas encontradas

A regra da 18.3 protegia módulos, mas `nova_questao` e `editar_questao` criavam e alteravam questões/gabarito sem lock conflitante no treinamento. Escritas ORM, remoções em cascade/delete-orphan e reassociações também escapavam. O SHARE do quiz sozinho não impedia escritas nas tabelas filhas.

Auditoria de referências a Questao/Alternativa, tabelas, relacionamentos e DML: há duas rotas administrativas de escrita; não há rota dedicada de exclusão nem serviço aplicativo de bulk DML do quiz. Portal e campanhas leem a avaliação. `simulacoes/snapshots.py` lê/trava treinamento, módulos, questões e alternativas; não modifica a prova. Uma atribuição a FK expirada pode perder o histórico ORM da origem: a implementação consulta também o vínculo persistido.

## 2. Regra central reutilizada

`estrutura_bloqueada` e `bloquear_sequencia`, em `app/utils/estrutura_treinamento.py`, continuam centrais. Existe início estrutural quando alguma participação possui `iniciado_em`, `ultimo_modulo_id` ou `concluido_em`. Esses mesmos campos, definidos uma única vez, também verificam participações novas/alteradas ainda pendentes no flush. Não há definição paralela para quiz. Participação apenas criada, tempo ativo ou heartbeat isolado não passam a definir início.

## 3. Operações congeladas

Todo campo de Questao/Alternativa é estrutural: enunciado, ordem, texto, correta, IDs e vínculos. Bloqueiam-se criação, exclusão, adição/remoção de alternativas, reassociação por FK ou relacionamento, remoções de órfãos e cascatas. Origem e destino são verificados. Não foi identificado atributo administrativo nesses modelos que permita exceção.

A nota mínima do treinamento permanece mutável, conforme decisão funcional explícita da auditoria 19.2A e snapshots da 19.1. Ela não altera questões, gabarito, acertos nem percentual obtido. Não foram modificados o cálculo de aprovação, resultados históricos nem a política de módulos.

## 4. Arquivos alterados

- `app/utils/estrutura_treinamento.py`: listener central ampliado, exceção/mensagem do quiz, resolução de vínculos antigos e novos, proteção de DML bulk via Session.execute.
- `app/treinamentos/routes.py`: precheck de interface nas duas rotas e handler com rollback, flash e redirecionamento.
- `tests/test_etapa192b.py`: cobertura funcional SQLite/PostgreSQL e corridas reais.
- `tests/test_portal_treinamento_quiz.py`: fixture de legado que simula prova cadastrada após conclusão usa SQL Core explicitamente apenas no teste; o comportamento histórico continua exercitado sem exigir escrita ORM agora proibida.
- `docs/etapa-19.2B-congelamento-quiz.md`: este relatório.

Não há alteração de templates, schema, configuração, modelos de tentativas ou certificados.

## 5. Locks e transações

O `before_flush` reúne treinamentos afetados por módulos e quiz, toma `FOR UPDATE` em IDs crescentes e consulta a regra após adquirir o lock, antes de escrever filhos. O lock permanece até commit/rollback, inclusive na criação em dois flushes (questão, depois alternativas). A verificação da rota melhora a interface, mas não é a garantia transacional.

Portal mantém participação `FOR UPDATE` → treinamento `FOR KEY SHARE` para início/avanço/heartbeat. Quiz mantém participação `FOR UPDATE` → treinamento `FOR SHARE`. Administração trava treinamento e apenas lê participações por MVCC: não acrescenta participação `FOR UPDATE` após treinamento. Snapshots de simulação preservam sua ordem treinamento → filhos. Nenhum commit intermediário foi acrescentado. Os vínculos do quiz são relidos após os locks; origem concorrente não incluída nas travas causa rejeição controlada para recarregar, sem adquirir novos locks fora da ordem. Não ocorreu deadlock nos cenários exercitados.

## 6. Comportamento antes do início

Todas as operações existentes de preparação nas rotas e ORM continuam funcionando: criação, edição, exclusão, ordem, alternativas e resposta correta. Mera participação criada continua editável mesmo com tempo ativo no fixture. A proteção de bulk rejeita Session.execute INSERT/UPDATE/DELETE de questões/alternativas e orienta usar o ORM transacional; nenhum caminho aplicativo atual usava essa API de escrita.

## 7. Comportamento após início

Mudanças efetivas provocam `AvaliacaoTreinamentoBloqueada`. As rotas desfazem a transação, retornam HTTP 302 no padrão administrativo e exibem: “Este treinamento já foi iniciado. Sua avaliação não pode mais ser alterada.” GET dos formulários também retorna a mensagem; visualização administrativa da prova continua disponível. A rejeição concorrente originada no flush também é tratada, sem 500.

Snapshots completos nos testes confirmam estabilidade de quantidade, IDs, vínculos, ordem, conteúdo, alternativas e gabarito. Treinamento com cursor legado ou conclusão permanece congelado. Atribuições ORM sem mudança efetiva não alteram a prova.

## 8. Concorrência

PostgreSQL 16 descartável, banco UTF-8, socket Unix local exclusivo, sem listener TCP. No cluster de testes foram desativados fsync e synchronous_commit para reduzir o custo de DDL; locks e transações continuam reais, sem ensaio de recuperação de falha física. Threads com sessões/conexões independentes e eventos SQL pausam a transação após aquisição real do lock. A segunda tenta o lock incompatível; o teste verifica que ainda não terminou antes de liberar a primeira.

Cobertura: início × criar questão; início × editar enunciado; início × trocar gabarito; primeiro avanço × editar; quiz enviado × gabarito; ordem inversa com edição legal antes do início; administração chegando antes do quiz após início; HTTP criação/edição passando pelo precheck e sendo rejeitados no flush. A correção do quiz conserva um acerto com o gabarito original. Também foi coberta reassociação concorrente que move a questão para origem posteriormente congelada. Uma edição legal concluída antes do início fica visível às operações posteriores. Na arquitetura atual um quiz elegível já exige módulos concluídos, portanto edição legal e quiz elegível não coexistem; teste da ordem inversa do quiz confirma rejeição da edição e snapshot preservado.

## 9. Banco e migrations

Nenhuma migration ou trigger criada. Reutilização dos locks e transações existentes é suficiente para os caminhos de escrita auditados. Migrations anteriores foram exercitadas somente em schemas temporários durante regressão. Não houve consulta ao banco DEV.

## 10. Testes

27 testes específicos aprovados, sem skips, em SQLite e PostgreSQL. Incluem sete métodos de concorrência, com dez corridas controladas. Nove testes JavaScript aprovados, sem skips. Evidências: `logs/etapa192b-final.log` e `logs/javascript.log`. Os 51 testes existentes de quiz também passaram.

Cobertura inclui criação/edição/exclusão antes do início, alternativas antes/depois, gabarito, reordenação, conclusão, participação apenas criada, IDs, órfãos, vínculos, FKs expiradas e início/edição no mesmo flush. SQL Core aparece apenas na fixture explícita de legado, não em serviço aplicativo.

Para reprodução, usar uma cópia completa do projeto (incluindo `log_privacidade.py` e `gunicorn_privacidade.py`), sem `.env` do DEV. Inicializar PostgreSQL descartável com `initdb --encoding=UTF8`, criar `/tmp/one-18.7E-review` e apontar `ONE_PROGRESSO_TEST_DATABASE_URL`, `ONE_HEARTBEAT_TEST_DATABASE_URL` e `ONE_APROVACAO_MIGRATION_TEST_DATABASE_URL` exclusivamente ao banco local. Executar `python -m unittest discover -s tests -v` e `node --test tests/test_portal_treinamento_heartbeat_browser.cjs`. A suíte específica usa `python -m unittest discover -s tests -p test_etapa192b.py`.

## 11. Regressões

Suíte Python completa: 979 testes aprovados, sem skips. PostgreSQL temporário UTF-8 configurado para todos os grupos PostgreSQL, incluindo migrations anteriores e concorrência. Evidência: `logs/suite-validada.log`.

Uma primeira execução da cópia não continha os módulos raiz de privacidade; a cópia foi completada. Outra execução usou banco SQL_ASCII e reproduziu falha na contagem de caracteres acentuados do feedback. O ambiente foi corrigido para UTF-8, sem alterar o feedback. A regressão identificou também um atributo indevido introduzido na lista de proteção dos módulos; foi corrigido e sua edição de conteúdo revalidada. As execuções substituídas foram interrompidas e a suíte inteira reexecutada. Foi criado também o diretório temporário exigido pela pipeline de logs das simulações; os dois casos dirigidos passaram. A execução limpa abaixo contém todos esses pré-requisitos. Os resultados acima correspondem à execução final completa.

A suíte cobre criação de treinamento, módulos, quiz, aprovação 19.1, conclusão sem quiz, feedback, painel, métricas, campanhas, convites e simulações. Os testes JavaScript de heartbeat continuam sem alteração. Sem execução de envio SMTP, ações GoPhish ou conexões externas. Avisos e logs de falha intencional dos testes não indicam erro no resultado da suíte.

## 12. Riscos residuais

A garantia se aplica às rotas e escritas ORM auditadas. SQL direto, Connection/Core DML e APIs legadas como bulk_save_objects fora do flush podem contornar os eventos; nenhum desses caminhos de escrita de avaliação foi encontrado nos serviços atuais. Não se declara imutabilidade contra intervenção de administrador de banco. Futuros serviços precisam usar o mesmo ORM e locks, ou justificar enforcement adicional no banco.

O predicado central conserva sua semântica atual: apagar/limpar deliberadamente todo histórico de início/progresso/conclusão pode deixar de satisfazê-lo. Não foi introduzido marcador permanente nem política de retenção nova. A ordenação global entre múltiplos flushes de serviços futuros e outros locks precisa ser auditada; os fluxos atuais tratados alteram um treinamento e o conjunto de locks de cada flush é ordenado.

SQLite verifica comportamento funcional; PostgreSQL real, no isolamento padrão READ COMMITTED, é a evidência de concorrência. Serviços futuros que mudem o isolamento precisam revalidar a estratégia. Código e testes foram preparados isoladamente; a proteção só passa a valer no DEV após aplicação explicitamente autorizada e atualização dos workers.

## 13. Recomendação para 19.2C

Após revisão/aplicação autorizada da 19.2B, usar questões/alternativas congeladas como pré-requisito de integridade. Preservar a ordem participação → treinamento, o SHARE da avaliação e o snapshot de critério por avaliação; depois desenhar identidade idempotente de envio, histórico e adaptação dos consumidores conforme 19.2A. Nenhuma implementação da 19.2C foi iniciada nesta entrega.
