# Etapa 19.2C2 — Adaptação dos consumidores para múltiplas tentativas

## Escopo e estado auditado

Implementação preparada em `/tmp/etapa192c2`, a partir de cópia do DEV `/opt/One_treino/app`. O pacote não foi aplicado no DEV. Auditoria local em transação somente de leitura confirmou o head `d192c1e4a909`, a coluna `numero_tentativa` e as constraints da C1, inclusive a UNIQUE individual real `resultados_quiz_participacao_id_key`.

Nesta execução foram reaproveitadas as fontes C2 já presentes nessa cópia temporária, após revisão dos consumidores e comparação da baseline com os arquivos ativos do DEV. Os testes específicos, a suíte Python completa e os JavaScript foram reexecutados; os logs entregues correspondem à revalidação. A entrega é um patch sobre a base C1, sem alteração dos arquivos operacionais.

Nenhuma migration foi criada ou alterada. A UNIQUE individual, a UNIQUE composta `uq_resultados_quiz_participacao_tentativa`, o CHECK positivo, o NOT NULL e o default 1 permanecem no modelo e no banco operacional. O portal grava somente tentativa 1, não fornece refação, não cria certificado e não implementa a C3. Nenhuma linha adicional foi criada no DEV.

## 1. Consumidores encontrados

| Consumidor | Dependência anterior | Adaptação |
|---|---|---|
| Modelo `ResultadoQuiz` | Backref `resultado_quiz`, `uselist=False` | Coleção ordenada `resultados_quiz`, sem lazy load |
| Portal do quiz | `scalar_one_or_none()` para resultado | Lê coleção ordenada explicitamente e seleciona representativo |
| Portal de feedback | Backref singular para link de retorno | Consulta `EXISTS` de resultado; feedback continua por participação |
| `utils/painel_colaboradores.py` | JOIN direto, contagens dependentes da UNIQUE; quiz recente bruto | Subquery representativa antes do JOIN; status/nota/critério/numeração por participação |
| Painel de colaboradores | Uma linha de quiz por participação | Uma linha representativa com número, total e ID da primeira aprovação |
| Detalhe de colaborador | Eager load singular, média e templates escalares | Eager load da coleção, média representativa e histórico estruturado |
| Dashboard | COUNT/AVG de linhas brutas, recentes brutos | COUNT/AVG por participação com quiz e recentes representativos |
| Campanhas/piloto | Backref singular, média e `.first()` arbitrário na decisão | Coleção carregada em lote e helper representativo |
| PDF da campanha | Nota e status do backref singular | Helper central; uma linha por participação |
| Templates de campanha e detalhe | Acesso direto ao resultado único | Helper explícito e resultado local por linha |
| Feedback, métricas de tempo, convites e simulações | Identidade/conclusão da participação | Sem mudança da cardinalidade desses registros; regressão verificada |

A busca abrangeu fontes Python e templates ativos, eager loads, backrefs, queries de `ResultadoQuiz`, campos de status e joins/agregados. Arquivos `.bak*`/`.orig` são backups, não pontos de entrada ativos. Scripts Python na raiz não apresentaram consumidores adicionais. As referências restantes a `resultado_quiz_id` são campos de projeção do painel, não relações singulares ORM. O único caminho operacional de criação de `ResultadoQuiz` permanece o POST do quiz.

## 2. Relacionamento final

`ResultadoQuiz.participacao` permanece uma relação escalar para a participação. Seu backref passa a ser `ParticipacaoTreinamento.resultados_quiz`, coleção ordenada por `(numero_tentativa, id)`, com `lazy="raise"`.

Não há alias ou propriedade `resultado_quiz` que escolha silenciosamente uma linha. Os consumidores ORM usam `selectinload(ParticipacaoTreinamento.resultados_quiz)` e os helpers recebem os dados já carregados. `resultados_carregados(p)` verifica o estado ORM e rejeita relação não carregada com `InvalidRequestError`, sem emitir SQL. Queries explícitas do portal continuam independentes do backref.

Essa mudança é somente de mapeamento ORM: não remove constraints nem muda a estrutura SQL. Código externo ao projeto que use o nome singular terá de migrar para a coleção e a seleção explícita; não manter um alias ambíguo foi uma decisão deliberada.

## 3. Helpers centrais e resultado representativo

Novo módulo `app/utils/resultados_quiz.py`:

- `ultima_tentativa`: maior número da tentativa, com ID como desempate determinístico.
- `primeira_aprovacao`: menor número com `aprovado is True`.
- `tem_tentativa_aprovada`: existência de primeira aprovação.
- `total_tentativas`: quantidade da coleção recebida.
- `resultado_representativo`: primeira aprovação; sem aprovação, última classificada; sem classificados, último histórico.
- `resumo_tentativas`: histórico ordenado, última tentativa, primeira aprovação, representativo e quantidade.
- `resultados_carregados`: exige carregamento explícito, sem I/O escondido.
- `resultados_representativos_sql`: mesma política em uma subquery de uma linha por participação.

O critério é a numeração, não o maior percentual, o maior ID ou o timestamp. O SQL usa `row_number()` por participação, com prioridade aprovado/reprovado/histórico, numeração crescente para aprovados e decrescente para os demais. `count() over (...)` fornece o total. Nenhuma nota é recalculada. Os helpers preservam objetos e snapshots existentes; `NULL` não é transformado em reprovação ou aprovação.

Templates usam helpers explícitos para selecionar o resultado e variáveis locais para exibir seus campos; não replicam a política de primeira aprovação/última classificação.

## 4. Primeira aprovação e aprovado_em

A referência para primeira aprovação é exatamente o menor `numero_tentativa` aprovado. Na sequência sintética reprovação/reprovação/aprovação/aprovação maior/reprovação/histórico, a tentativa 3 continua sendo a primeira aprovação e o representativo, mesmo com 100% na tentativa 4.

Os testes confirmam, no cenário consistente, `primeira_aprovacao.concluido_em == participacao.aprovado_em`. Uma data inconsistente sintética não muda a tentativa identificada nem provoca reparo retroativo. As leituras não sobrescrevem `aprovado_em`, notas ou classificações. Em treinamento sem quiz pode existir aprovação persistida sem tentativa, logo não há ID de primeira tentativa aprovada a inventar.

A C3 deverá manter numeração monotônica, proteger alocação/escrita com FOR UPDATE da participação e persistir `aprovado_em` apenas na primeira aprovação. O timestamp é histórico; não deve virar data da melhor nota ou da última tentativa.

## 5. Status central Python × SQL

A lógica da 19.1D em `painel_colaboradores.py` foi evoluída sobre o resultado representativo. Como esse resultado é aprovado se existe qualquer aprovação, as condições ficam equivalentes para objetos e SQL:

| Estado | Regra |
|---|---|
| Aprovado | Existe tentativa `aprovado=True`; ou, sem resultado de quiz, existe `aprovado_em` persistido |
| Reprovado | Concluído, com resultado classificado e sem aprovação |
| Histórico | Concluído, sem aprovação e sem classificação conhecida |
| Em andamento | Não concluído, iniciado e sem aprovação persistida |
| Não iniciado | Não concluído, não iniciado e sem aprovação persistida |

O caso concluído sem resultado e sem `aprovado_em` continua histórico. Históricos NULL nunca sobrepõem avaliação classificada. Um histórico com `aprovado_em` inconsistente não é convertido em tentativa aprovada.

Diferença deliberada exigida pela regra C2: uma aprovação de quiz persistida prevalece mesmo em um registro legado sem `concluido_em`. A 19.1D exigia conclusão também nesse caso. Há teste explícito Python × SQL, preservando os campos originais. O fluxo real conclui e classifica na mesma transação e não produz essa inconsistência. Contadores de conclusão continuam baseados em `concluido_em`; não inventam conclusão para reconciliar dados legados.

`status_participacao` aceita a coleção já carregada ou um resultado explicitamente pré-selecionado pela política central, por exemplo no dashboard SQL. `situacao_participacao` exige a coleção carregada quando seu argumento explícito não é fornecido.

## 6. Métricas sem multiplicação

Contagens de participações, status, taxas, soma de tempo e notas usam a subquery representativa antes de JOIN/AVG/SUM. Tempos continuam agregados por participação. Assim reprovação/reprovação/aprovação conta uma participação aprovada, sem contar as reprovações anteriores como participações reprovadas e sem multiplicar tempo.

A consulta de última atividade ainda pode incluir todos os eventos de tentativa em UNION ALL seguido de MAX: isso mede atividade e não multiplica os indicadores de participação. As métricas de capacitação/feedback continuam baseadas em participação, tempo agregado e feedback único, sem JOIN adicional de linhas brutas de quiz.

Na C2 não são expostas novas métricas de tentativas. `total_tentativas` é informação da participação; COUNT/AVG globais de quiz agora explicitamente são por resultado representativo de participação. Um futuro painel de tentativas deverá usar queries separadas, identificadas como tal.

## 7. Painel de colaboradores

Mantém paginação, filtros e uma linha por colaborador. A projeção de participações contém status, nota e critério da tentativa representativa, `numero_tentativa`, `total_tentativas` e `primeira_aprovacao_id`. A identificação de quiz recente por colaborador opera sobre representativos, ordenados por data e ID.

Interface mínima: informa quantidade e número representativo junto do resultado. Com o banco operacional da C1, ambos continuam 1. A consulta não depende mais da UNIQUE individual para impedir duplicações.

## 8. Detalhe do colaborador

Eager load da coleção em lote. O contexto `tentativas_por_participacao` fornece histórico ordenado, primeira aprovação, última tentativa, resultado representativo e total. A média exibida usa um representativo por participação, sem ponderação pelo número de tentativas.

A interface acrescenta apenas “Tentativas” e “Tentativa representativa”; status e snapshots continuam nas áreas existentes. Não há botão de refação. O backend já entrega o histórico completo para uma apresentação futura sem remodelar o relacionamento.

## 9. Dashboard

“Resultados recentes” significa **resultado representativo recente por participação**, ordenado por `concluido_em DESC, id DESC`, limitado a dez. Não significa últimas dez tentativas brutas. Isso preserva a semântica atual de uma linha por participação com quiz e evita que uma pessoa com muitas tentativas domine a lista.

COUNT e AVG globais/departamentais usam a mesma subquery. A média é do percentual representativo por participação, incluindo históricos como já ocorria. A taxa de aprovação usa o status persistido e exclui históricos sem classificação. Quantidade de quizzes significa participações com resultado. Com uma tentativa, os valores permanecem equivalentes aos anteriores.

## 10. Campanhas, decisão do piloto e PDF

Carregamento da coleção substitui o singular. Médias e totais de quizzes operam sobre um representativo por participação. A decisão do piloto substitui a busca `.first()` por seleção explícita dos resultados carregados em lote. Não há escolha arbitrária nem query por tentativa.

Templates de detalhe/relatório/análise usam a mesma seleção central. O PDF recebe participações com relacionamentos carregados, escolhe a nota representativa e emite uma única linha por participação. O teste captura a tabela ReportLab e verifica quantidade de linhas, status e percentual da primeira aprovação, em vez da nota máxima posterior.

## 11. Feedback

Feedback permanece único e associado à participação. O link de retorno no portal usa EXISTS, suficiente para indicar que há resultado de quiz sem escolher uma linha arbitrária. Não depende do relacionamento singular nem do número de resultados.

Os testes inserem tentativas posteriores somente no schema descartável e confirmam que feedback original, ID, comentário e timestamp permanecem; reenvio não cria outro feedback. GET/POST de feedback continuam funcionando e retornam ao quiz.

## 12. Performance

Medição PostgreSQL real, incluindo renderização dos templates, com uma e depois cinco tentativas por participação:

| Página | 1 tentativa | 5 tentativas |
|---|---:|---:|
| Painel | 5 | 5 |
| Dashboard | 29 | 29 |
| Detalhe | 8 | 8 |

O número de queries não cresce com tentativas. O teste da coleção não carregada verifica zero SQL na rejeição. Testes anteriores continuam verificando crescimento de participantes/paginação e ausência de N+1.

O volume de linhas processadas pela janela SQL e a memória para histórico ORM crescem com o volume de tentativas; orçamento constante de queries não significa tempo/memória constantes. O dashboard já possui consultas por departamento; esse fator preexistente permanece. Não há benchmark de volume operacional massivo nesta etapa.

## 13. Testes e regressão

Resultados finais: **20 testes específicos**, **1013 testes Python na suíte completa** e **9 testes JavaScript aprovados, sem skips**. Logs finais em `logs/especificos.log`, `logs/suite-python.log` e `logs/javascript.log`. A suíte completa inclui os específicos. Nenhuma regressão não planejada foi identificada; a mudança explícita de classificação de aprovações persistidas sem conclusão é coberta em Python e SQL.

Os 20 específicos cobrem helpers puros; uma tentativa e reprovações múltiplas; somente históricos; histórico/classificado/histórico; primeira aprovação seguida de maior nota, reprovação e histórico; ordem numérica independente de timestamp; consistência e divergência de `aprovado_em` sem reparo; Python × SQL; contagens/tempos sem multiplicação; painel; detalhe/histórico; dashboard; campanhas/templates; PDF; feedback; orçamento 1/5; relação sem lazy load; consumidores somente de leitura; portal sem tentativa 2 e replay idempotente; GET/POST multicardinalidade sintética; feedback do portal com tentativas posteriores.

PostgreSQL 16 foi iniciado de forma temporária com UTF-8, somente socket Unix `/tmp/etapa192c2/pgsocket`, porta 55494 e TCP desabilitado. As fixtures criam schemas descartáveis. Apenas nos schemas cujo nome começa por `painel_test_` ou `progresso_test_` os testes C2 removem temporariamente a UNIQUE individual; a composta e os CHECKs são preservados. A rotina impede usar `public`/schema operacional. As migrations C1 continuam testadas no PostgreSQL e equivalentes aos metadados; o mapeamento coleção não altera schema.

Nenhuma conexão externa, SMTP real, GoPhish real ou produção. A suíte completa inclui regressões de criação, módulos, quiz, aprovação, conclusão sem quiz, feedback, painel, métricas, campanhas, convites, simulações, proteção estrutural B e testes reais de concorrência anteriores.

Comando de reprodução, na cópia isolada:

```sh
export ONE_PROGRESSO_TEST_DATABASE_URL='postgresql+psycopg://oneti@/postgres?host=/tmp/etapa192c2/pgsocket&port=55494'
export ONE_HEARTBEAT_TEST_DATABASE_URL="$ONE_PROGRESSO_TEST_DATABASE_URL"
export ONE_APROVACAO_MIGRATION_TEST_DATABASE_URL="$ONE_PROGRESSO_TEST_DATABASE_URL"
/opt/One_treino/app/.venv/bin/python -B -m unittest discover -s tests -p 'test_etapa192c2.py'
/opt/One_treino/app/.venv/bin/python -B -m unittest discover -s tests
node --test tests/test_portal_treinamento_heartbeat_browser.cjs
```

## 14. Arquivos, entrega e riscos

Fontes alteradas: modelo ResultadoQuiz; helpers novos; utilitário do painel; rotas de portal, colaboradores, dashboard e campanhas; PDF; templates de painel/detalhe e três templates de campanha. Testes anteriores de C1/status/apresentação/tempo/feedback foram adaptados à coleção explícita, preservando verificação de default/UNIQUE/schema/fluxo legado; a expectativa de aprovação sem conclusão foi atualizada à regra C2. Novo teste específico e este documento completam o patch. Lista e hashes exatos no manifesto da entrega.

Entrega solicitada: `/home/oneti/etapa192c2/etapa192c2.patch` e `/home/oneti/etapa192c2/RELATORIO.md`, com manifesto, fontes e logs complementares. Patch será conferido em cópia descartável, com hashes das fontes testadas. DEV verificado novamente contra a base ao final. Não se aplica patch, migration nem reload de workers no DEV nesta entrega.

Riscos residuais: integrações/scripts externos não auditáveis que usem backref singular devem ser migrados; leitores novos devem carregar coleção explicitamente; históricos inconsistentes são exibidos sem correção e podem exigir auditoria de dados separada; a seleção por número pressupõe futura numeração monotônica; queries constantes não eliminam custo proporcional de volume/histórico. Não há restrição adicional de banco/migration nesta C2.

Para uma aplicação futura autorizada, atualizar os arquivos de forma coordenada e recarregar workers para não misturar templates novos e mapeamento antigo. Nenhuma migration é necessária; o head continuará `d192c1e4a909` e a UNIQUE individual continuará limitando as escritas a uma linha. Fora do projeto, substituir acesso singular por coleção/seleção explícita antes de liberar N linhas.

## 15. Preparação necessária para C3

A C2 prepara leitura de N resultados, mas **não autoriza nem implementa** refação. A C3 ainda precisará:

1. Definir elegibilidade, limites e identidade idempotente de cada envio/tentativa. Um POST repetido não pode virar próxima tentativa apenas porque já há um resultado.
2. Implementar alocação monotônica sob FOR UPDATE da participação: validar elegibilidade e replay após obter a trava; calcular o próximo número no mesmo escopo transacional; inserir/registrar aprovação mantendo a trava até commit. Todos os escritores devem seguir o protocolo.
3. Preservar a ordem participação → treinamento e a trava SHARE do treinamento no POST para obter a avaliação congelada. Não mudar os locks da B nem sobrescrever os snapshots antigos.
4. Persistir primeira aprovação uma vez; não substituir timestamp/ID lógico pela melhor nota ou por aprovação posterior; esclarecer encerramento/conclusão quando houver refação.
5. Somente então criar a migration específica de remoção da UNIQUE individual, mantendo a composta e os CHECKs e definindo rollback que não descarte tentativas. Testar concorrência real PostgreSQL de alocação, replay e corridas.
6. Confirmar métricas representativas, listas/recentes e qualquer integração externa; decidir separadamente se haverá UI de histórico e métricas de tentativas. Feedback continuará único.

Certificado não foi criado; a primeira aprovação é apenas referência de leitura preparada para um consumidor futuro. A C3 não foi iniciada.
