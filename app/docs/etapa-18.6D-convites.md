# Etapa 18.6D — administração e worker fake

Proposta preparada sobre o código real, sem aplicação no diretório original.
Sem SMTP real, daemon, retry automático, reconciliação ou implementação da 18.6E.

## Interface e fluxo

Página central `/convites/`, obrigatoriamente com um dos filtros:
`campanha_id`, `treinamento_id` ou `participacao_id`. Links no detalhe da campanha
e na linha da participação no detalhe do colaborador. Somente administrador
ativo autenticado, conforme o padrão de autorização de campanhas. Os links
também são restritos a esse perfil. Não cria participações nem modifica campanha,
piloto, progresso, quiz, tempo ou feedback.

1. GET lista somente participações existentes do contexto, estado mais recente
   e elegibilidade. Até 100 linhas por página, navegação explícita. Cada opção
   contém um snapshot assinado de ID, destinatário normalizado, nome, título e
   IDs do colaborador, treinamento e campanha. Sem token de acesso ou URL.
2. POST de seleção, com CSRF, verifica assinatura, usuário, prazo de 30 minutos,
   escopo comum e ação explícita: primeiro envio ou reenvio. Revalida todos os
   itens. Mostra quantidades, destinatários, treinamento, remetente, assunto e
   texto com `[link individual oculto]`. Mudança desde a seleção é excluída com
   motivo. Nenhuma solicitação é persistida nessa fase.
3. POST de confirmação, com CSRF, verifica o envelope assinado e revalida no
   servidor dentro da transação de persistência. Bloqueia participações e
   depois colaboradores/treinamentos em ordem global por ID. Compara e-mail,
   nome e contexto ao que o administrador viu. Remetente alterado exige nova
   seleção. Itens inelegíveis são excluídos individualmente; itens excluídos
   na prévia permanecem excluídos nesta confirmação.
4. Recibo, solicitações pendentes e auditoria são commitados juntos. A resposta
   mostra IDs registrados e razões de exclusão, com links para acompanhamento.
5. Worker faz reserva curta pela 18.6B (`FOR UPDATE SKIP LOCKED`), revalida,
   registra tentativa, libera a transação, compõe em memória pela 18.6C e
   processa pelo fake. Finaliza em outra transação curta. Não há lock durante
   transporte nem retenção de mensagem, token ou link nos novos registros.

Os formulários de envio individual e de lote funcionam sem JavaScript.
Não há mutações por GET. Respostas de convites usam `Cache-Control: no-store`.
Snapshot assinado é um dado do formulário, não o token de acesso ao treinamento.

## Idempotência e migration

Nova migration `e186d7e4a904`, sobre `d186b7e4a903`, cria apenas
`confirmacoes_convites`: UUID como PK, administrador, digest do envelope,
resultado JSON com IDs e códigos de exclusão, data. Não armazena snapshot de
e-mail, nome, mensagem, URL, token ou segredo. Migrations anteriores intactas.

Cada confirmação recebe UUID, e cada participação recebe um UUID5 derivado
da confirmação e do ID da participação. A PK do recibo serializa confirmação
concorrente: a segunda transação espera o commit da primeira e devolve seu
resultado persistido. Duplo clique não repete envio, auditoria ou exclusões,
inclusive depois de processamento/cancelamento. UUID usado com outro envelope
ou administrador é rejeitado. Uma falha SQL reverte tudo.

O serviço existente da 18.6B foi extraído para uma função que aceita Session;
as APIs anteriores continuam abrindo suas próprias transações e preservam
as regras. Confirmações diferentes disputando participações são serializadas
pelos locks: primeiro envio jamais vira reenvio implícito, e reenvio exige
solicitação anterior terminal. Resultado indeterminado exibe aviso explícito
de possível duplicidade na confirmação do reenvio.

## Operação

Depois de aprovar e aplicar o patch, executar a migration antes de acessar
a interface. Não alterar `.env` como parte desta proposta.

O comando exige `MAIL_BACKEND=fake`, `MAIL_ENABLED=true`, `BASE_URL` confiável
e remetente institucional válido; essas variáveis são verificadas antes de
reservar qualquer solicitação. Padrões da 18.6C continuam desabilitado/fake.
Configurado SMTP, o comando bloqueia antes da reserva, sem criar cliente SMTP.

    flask --app run convites processar
    flask --app run convites processar --limite 5
    flask --app run convites processar --resultado-fake falha
    flask --app run convites processar --resultado-fake resultado_indeterminado

Por execução, processa no máximo `min(--limite, MAIL_BATCH_SIZE)` ou
`MAIL_BATCH_SIZE` sem opção. A allowlist da 18.6C continua aplicada; não troca
destinatários. Saída CLI contém somente backend e quantidade processada.
`MAIL_RATE_LIMIT` continua reservado para futura etapa operacional.

Fila vazia é sucesso com zero processados. Falha de elegibilidade conta no
limite e não interrompe os itens seguintes. Estados terminais não voltam à fila.
Se o processo morrer após reservar, o item permanece em processamento, conforme
o contrato da 18.6B; não presumir que o transporte falhou nem reenviar automaticamente.
CLI é uma operação local do administrador do servidor, sem endpoint HTTP de
processamento nem daemon permanente.

## Status, histórico e cancelamento

Não enviado (sem solicitação), pendente, em processamento, enviado, falha,
resultado indeterminado e cancelado. “Enviado” significa aceito pelo transporte,
nunca entregue ou lido. Nesta etapa o aceite é simulado.

Histórico por participação: sequência, primeiro envio/reenvio, endereço usado,
data da solicitação, data de processamento/finalização, estado e código resumido
da última tentativa. Não inclui mensagem, token, link, segredo ou resposta bruta.

Cancelar usa POST e CSRF, com administrador ativo e auditoria, somente pendente.
Cancelar novamente um cancelado é idempotente; em processamento ou terminal
não pode ser cancelado. O histórico permanece.

## Validação

`tests/test_admin_convites.py` exercita formulários reais, autenticação, CSRF,
snapshots assinados, duas janelas de alteração do e-mail, elegibilidade, exclusões,
primeiro envio/reenvio, lote, recibos, histórico, cancelamento, CLI, limites,
fake e privacidade. Concorrência e liberação de locks usam PostgreSQL em schemas
isolados. Testa metadata e upgrade/downgrade/reupgrade da nova migration.
O teste de feedback apenas reconhece o novo head; a verificação da migration
específica anterior continua preservada.

Executar em banco exclusivo de testes, nunca com DATABASE_URL de produção:

    PGCLIENTENCODING=UTF8 ONE_PROGRESSO_TEST_DATABASE_URL=... ONE_HEARTBEAT_TEST_DATABASE_URL=... python -B -m unittest discover -s tests -v
    node --test tests/test_portal_treinamento_heartbeat_browser.cjs
