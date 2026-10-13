# Etapa 18.7C — proposta de execução e preparação auxiliar

Proposta preparada em cópia isolada; aplicação e banco originais não alterados.
Nenhum serviço Gophish, SMTP ou campanha executável foi iniciado. A integração
continua desabilitada por padrão. Resultados/eventos e landing pública pertencem
à etapa seguinte e não constam deste patch. Convites da 18.6 permanecem independentes.

## Arquitetura e aprovação

Aprovação de `tipo == simulacao_phishing` chama `aprovar_simulacao` e cria a
revisão **na mesma transação local** que muda o estado da campanha. O lock
`FOR UPDATE` da campanha serializa aprovações concorrentes. Falha na criação do
snapshot desfaz também a aprovação. Repetir a aprovação de uma campanha já
aprovada devolve a mesma revisão, sem nova auditoria ou transporte.

Administrador ativo, distinto do criador, continua obrigatório. A solicitação
usa o fluxo local da 18.7B. Campanhas normais não passam por estes serviços.
Registros legados sem tipo explícito não adquirem revisões automaticamente.
Campanhas já aprovadas antes da migration precisam de reabertura e nova aprovação
humana; não se presume que seu estado atual represente uma aprovação anterior.

Uma reabertura administrativa confirmada retorna a campanha a rascunho, conserva
as revisões e execuções anteriores e exige uma nova solicitação/aprovação. O novo
ciclo de aprovação cria uma revisão numerada, mesmo quando deliberadamente
reaprovada a mesma configuração. Isso é distinto de repetir o POST de aprovação:
a repetição desse POST nunca cria outra revisão.

## Dados e modelos

| Modelo / tabela | Conteúdo e integridade |
| --- | --- |
| RevisaoSimulacao / revisoes_simulacao | Campanha, número, SHA-256, snapshot canônico Text, aprovação, cinco referências locais. Unique campanha/número; número positivo; estado somente aprovada. |
| ExecucaoSimulacao / execucoes_simulacao | UUID público, revisão, piloto/ampliada, sequência, seis estados locais, responsável, datas, instância, hashes de origem e público. Unique revisão/fase e UUID. Uma execução por fase por revisão; mudança de escopo exige nova revisão. |
| AlvoSimulacao / alvos_simulacao | Execução, colaborador, participação já existente opcional, e-mail aprovado, nome nullable não utilizado, identificador externo futuro nullable, estado. Uniques execução/colaborador, execução/e-mail e execução/identificador externo. |
| RecursoExternoSimulacao / recursos_externos_simulacao | Grupo/template/landing_page, execução/revisão/instância, nome técnico exclusivo, ID externo nullable, digest, estado, criação incerta, reconciliação e código. FK composta comprova execução/revisão/instância. Uniques execução/tipo, instância/tipo/ID e instância/tipo/nome. |
| OperacaoSimulacao / operacoes_simulacao | UUID idempotente, execução, preparar/reconciliar, responsável, estado, tentativa, início/fim, código e resultado mínimo com IDs. UUID unique; índice unique parcial impede duas operações processando na mesma execução. |

Foreign keys usam RESTRICT; índices cobrem campanha, revisão, execução,
colaborador, participação e instância. Checks restringem estados, fases, tipos,
IDs positivos, números e tamanho dos hashes. Revisão/identidades são protegidas
por before_flush no ORM e triggers PostgreSQL contra UPDATE/DELETE SQL direto.
A participação precisa corresponder à campanha, colaborador e treinamento.
O trigger de alvo exige e-mail e participação exatamente presentes na revisão.

A ONE guarda governança, público autorizado, snapshots, treinamentos, vínculos,
operações e auditoria. O Gophish recebe somente o grupo com e-mails aprovados e
os conteúdos aprovados do template/página, sem cargo, departamento, nome pessoal
ou token. Nomes externos são técnicos: UUID de execução, tipo e digest.
O fake guarda somente IDs, nomes técnicos, hashes e flags; não conserva HTML,
e-mails ou tokens. Seu estado em memória é descartável, exclusivamente para teste/DEV.

Nenhum dos cinco modelos contém senha, chave de API, token de participação ou
corpo de resposta. A participação existente é referenciada somente por ID;
nenhum convite ou participação é gerado automaticamente. Campos legados de IDs
permanecem intactos e nunca são promovidos para o novo fluxo.

## Snapshot e público congelado

JSON canônico: chaves ordenadas, separadores compactos, UTF-8, sem NaN. SHA-256
sobre os bytes desse JSON. Inclui IDs locais, criador, nome, digest da descrição,
datas **planejadas** da configuração (normalizadas UTC), público, exclusões,
conteúdos aprovados, cenário e digest da estrutura do treinamento/quiz.
Datas de criação/aprovação/auditoria não entram no hash. Respostas de quiz não são
copiadas para o snapshot; sua configuração entra somente por digest.

A propriedade snapshot devolve uma cópia. A revisão persistida nunca é editada.
PostgreSQL verifica o hash dos bytes na inserção e rejeita edição/remoção.
Conteúdo é submetido ao mesmo validador conservador da 18.7B, sem exceções novas.
Tokens longos de participações já existentes e a chave configurada são rejeitados
se encontrados nos campos exportáveis. Links do portal por token também são
rejeitados. Isso não substitui revisão humana do conteúdo aprovado.

Público ordenado por ID local, deduplicado por colaborador. Inativos são excluídos
com motivo local, inclusive quando seu e-mail está vazio. Ativos com e-mail
vazio/inválido ou duplicado após normalização bloqueiam toda aprovação, em vez de
serem silenciosamente excluídos. O cadastro real já proíbe NULL de e-mail; o
serviço também o rejeita. Validação é sintática, sem consulta DNS/entregabilidade.
`.test` é permitido somente fora de production. Não se usa e-mail como identidade
global: alvos sempre mantêm colaborador_id.

Piloto seleciona explicitamente 1–3 colaboradores aprovados; IDs repetidos não
duplicam alvos. Ampliada usa todo o público, sem seleção adicional. Depois de
aprovado, alterações de grupo, e-mail, atividade ou configuração relevante
bloqueiam criação/preparação com código sanitizado. Os alvos antigos não mudam.
A reconciliação consultiva de histórico continua possível; ela não autoriza
novos POSTs. Uma participação que surgiu depois da aprovação não é incorporada
silenciosamente ao snapshot.

## Instância, ambiente e conteúdo

Nova configuração `GOPHISH_INSTANCE_ID`: identificação estável, pública e não
secreta, até 64 caracteres `[A-Za-z0-9_.-]`, iniciando por alfanumérico. O fake
usa `one-fake` por padrão; HTTP habilitado exige valor explícito. Não aceita valor
igual à chave. URL HTTPS continua validada sem credenciais, query ou fragmento.
Somente seu hash de origem é persistido; a chave nunca participa desse hash.

Trocar instance_id ou reatribuir a mesma identificação a outra URL/backend
bloqueia execuções existentes. IDs só têm significado dentro de instância/tipo.
TLS continua obrigatório, CA do sistema ou CA explícita, sem verify=False.
HTTP de preparação é bloqueado em DEV e permitido somente em production,
homologation ou homologacao. Isso adiciona uma cerca de ambiente, não uma nova RBAC.
GOPHISH_ENABLED deve ser true e a configuração válida. GOPHISH_SEND_ENABLED
continua false incondicionalmente. Preparação nunca utiliza campaign, smtp,
sending profile, agendamento ou mensagem.

Formulários, captura de credenciais/senhas, scripts e login imitado permanecem
proibidos. A página sincronizada é apenas o HTML local seguro aprovado; nenhuma
rota educativa pública ou ponte para participação é criada nesta etapa.

## Idempotência, transações e reconciliação

1. Sessão própria curta; campanha/revisão/execução/recursos bloqueados, usuário,
   configuração, instância, público e conteúdo revalidados.
2. UUID público é vinculado a execução/ação/responsável. Reserva processando e
   auditoria são commitadas. A mesma chave retorna o resultado salvo; outra
   chave durante processamento retorna busy. Tentativa é sequência histórica,
   nunca contador de retry automático.
3. Transporte ocorre fora de qualquer transação desses serviços. A rota encerra
   também a transação de autenticação antes de chamar a operação.
4. Permissão, configuração e snapshot são revalidados novamente antes de cada
   POST, numa transação curta. Revogação durante uma consulta bloqueia a criação.
   Cada intenção de POST é persistida como criação incerta antes do transporte.
   Capability interna assinada, curta e não exposta autoriza somente o payload
   e o endpoint auxiliar daquela reserva. Mutações HTTP avulsas continuam vedadas.
5. Sessão nova valida DTO mínimo, nome técnico e digest; grava ID somente inteiro
   positivo, finaliza recurso/operação e audita códigos constantes.

Não se altera ou compartilha recurso de outra execução. Nunca se faz PUT.
Consulta de ID conhecido exige correspondência de nome e conteúdo, não apenas
existência. Nome técnico exclusivo permite localizar criação que perdeu resposta.
Lista é filtrada pelo nome esperado; duplicidade ou digest divergente bloqueia.

| Resposta | Conduta |
| --- | --- |
| 200 válido | Conferir ID, nome e digest; reutilizar vínculo exato. |
| GET por ID com 404 | Marcar ausência confirmada e remover vínculo antigo. Preparação que encontrou o 404 para; somente ação posterior explícita pode criar substituto. |
| Lista vazia, nunca criado | Pendente, não comprova um 404. Pode realizar a primeira criação aprovada. |
| 401 / 403 | auth_error / forbidden, falha; nenhum POST causado pela consulta. |
| Timeout / conexão incerta | resultado_indeterminado; nenhum retry automático. |
| TLS | tls_error, falha fechada. |
| 5xx de consulta | server_error, falha operacional; não recria. |
| JSON inválido / ID ausente | invalid_response; não vincula ID nem expõe corpo. |
| POST incerto, 5xx ou resposta inválida | Preserva possibilidade de criação e exige reconciliação. Estado é mais conservador que uma simples falha operacional. |

Depois de POST incerto, reconciliação somente GET pode localizar o nome/digest e
vincular o ID. Lista vazia não prova ausência e mantém indeterminado. Não se
repete POST automaticamente ou por novo UUID enquanto a incerteza permanece.
Reconciliação de ID conhecido indisponível igualmente não transforma timeout,
401, 403, TLS, 5xx ou JSON inválido em ausência.

## Interface e auditoria

`/simulacoes/campanhas/<id>` é administrativa: revisão/hash, fase/sequência,
quantidade, instância, estados, IDs auxiliares, última operação/código e data
da última reconciliação. Mostra IDs locais do público para seleção do piloto;
não mostra e-mails, tokens, HTML, chave ou erro bruto. Cache-Control no-store.

Representar execução, preparar, reconciliar e reabrir usam POST+CSRF, preview
assinado para usuário/ação/escopo e confirmação explícita. GET não sincroniza.
O antigo POST preparar apenas redireciona simulações explícitas para esta tela;
não realiza preparo direto. Campanha permanece aprovada após preparo auxiliar.
Não há botão de disparo. Sincronizações avulsas permanecem bloqueadas como na B.

Reutiliza o decorador administrador existente e revalida o usuário no banco.
Não cria RBAC por departamento/empresa. Auditoria registra criação de revisão e
execução, solicitação, reconciliação e finalização/falha/indeterminação; somente
IDs, fases, contagens e códigos. DTOs não contêm corpo ou exception string.

## Migration e testes

Migration aditiva `f187c7e4a905`, parent `e186d7e4a904`; nenhuma migration histórica
é alterada. Não depende de rede/dados externos, nem converte IDs legados.
Triggers usam PL/pgSQL e sha256(bytea) nativos, validados em PostgreSQL 16.
SQLite suporta testes de contrato/ORM; as proteções SQL e concorrência operacional
requerem PostgreSQL com essa função nativa, não equivalência de locks SQLite.
Downgrade remove exclusivamente estas tabelas/função/triggers e perde o histórico
novo; não deve ser utilizado em produção sem decisão sobre retenção desses dados.

`tests/test_simulacao_preparacao.py` roda contratos em SQLite e PostgreSQL:
aprovação/revisões, público/imutabilidade, instância, permissões, confirmação,
auxiliares apenas, reconciliação/status, timeout antes/depois da criação, sucesso
sem ID, dados sensíveis, cercas de processo e concorrência. PostgreSQL verifica
locks NOWAIT em outra conexão durante cada transporte fake, demonstrando que
execução/recursos não ficam bloqueados por transação durante transporte.
Testa stamp B -> upgrade C -> downgrade B -> reupgrade C via Alembic real.

Execuções e reconciliações dos testes novos utilizam somente FakeBackend.
Regressões existentes de TLS/cliente usam HTTP mockado, nunca uma API real.
Runner de revisão bloqueia requests, SMTP e sockets TCP; PostgreSQL usa socket
local em cluster descartável. Foram desabilitados fsync/synchronous_commit **apenas
nesse cluster descartável** para acelerar a suíte; não é teste de queda de energia
ou durabilidade física. O banco da aplicação não foi acessado.

## Limitações e decisões antes da 18.7D

- Queda de processo após reserva mantém operação processando e bloqueia novas
  operações. Não há lease/retry ou liberação automática. Recuperação operacional
  exige confirmar o encerramento do executor, encerrar administrativamente a
  reserva como indeterminada em manutenção controlada e só então reconciliar.
  Antes de qualquer worker/disparo futuro, definir recuperação com fencing e responsabilidade
  operacional; idade da operação sozinha não comprova que um executor parou.
- POST incerto sem recurso localizável permanece bloqueado. A D precisa definir
  tratamento manual documentado para ausência não comprovada e cancelamento.
- Definir versão/API Gophish e normalização efetiva dos recursos em homologação
  autorizada antes de ativar HTTP. Nenhuma compatibilidade com servidor real foi
  comprovada nesta revisão offline.
- Antes da 18.7D, definir vínculo explícito e exclusivo entre execução e campanha
  externa, com instância e aprovação. A C não possui campanha externa nem importa
  IDs legados; importar resultados sem esse vínculo deve continuar bloqueado.
- Definir identidade futura de resultados por execução/instância/identificador
  externo de alvo, deduplicação de eventos e mapeamento explícito de participação
  que não existia no snapshot; token nunca vai para o Gophish. Participação
  congelada não pode ser trocada por edição silenciosa.
- Definir ponte educativa ONE e acesso seguro ao treinamento, retenção de dados,
  abertura opcional/rastreamento e governança de passagem piloto -> ampliada.
  Nenhum evento poderá concluir treinamento automaticamente.
- Uma execução por fase/revisão e piloto até três alvos são limites conservadores
  desta proposta. Repetições ou mudança de seleção exigem nova aprovação/revisão.
  Qualquer flexibilização requer decisão explícita e testes de governança.
