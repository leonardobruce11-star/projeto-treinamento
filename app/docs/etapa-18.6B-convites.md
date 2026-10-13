# Etapa 18.6B — persistência e regras dos convites

Esta etapa contém modelos, migration, serviços transacionais e transporte falso.
Não possui SMTP, URL pública, composição de mensagem, endpoints, interface de
envio, daemon ou agendamento. Esses recursos não são necessários para testar as
regras de persistência. A 18.6C não está implementada.

## Migration e modelo

Revision `d186b7e4a903`, down_revision `c185b7e4a902`. O head anterior foi
confirmado no código e no banco real, em transação somente leitura. As migrations
anteriores permanecem intactas.

`envios_convite_treinamento` registra participação, sequência, tipo, administrador,
snapshot do destinatário, UUID da confirmação, estado, versão e datas.
`tentativas_envio_convite` registra envio, número, datas, resultado, código de
erro normalizado e UUID de correlação `message_id`. Esse UUID não representa um
cabeçalho SMTP nesta subetapa; não há mensagem MIME ou transporte real.

Constraints:

- UNIQUE (participacao_id, sequencia); sequência positiva.
- UNIQUE global do identificador_confirmacao, armazenado como UUID.
- Primeiro envio tem sequência 1; reenvio tem sequência maior que 1.
- Estados limitados a pendente, em_processamento, enviado, falha,
  resultado_indeterminado e cancelado. Não enviado é ausência de solicitação.
- Datas de processamento/finalização coerentes com cada estado.
- FKs RESTRICT para participação, administrador e envio da tentativa.
- UNIQUE (envio_id, numero_tentativa), número positivo e message_id único.
- Índice único parcial: no máximo uma tentativa aberta por envio.
- Resultado de tentativa limitado a enviado, falha e resultado_indeterminado;
  NULL significa tentativa aberta. Resultado e finalização são coerentes.
- Sucesso não possui código de erro; falha/indeterminação exigem código da lista
  fechada. Nenhuma resposta bruta ou exceção é aceita como código.
- Índice (estado, id) para seleção da fila.

## Solicitação e idempotência

`solicitar_primeiro_envio(participacao_id, usuario_id, identificador_confirmacao)`
e `solicitar_reenvio(...)` são ações distintas. Exigem administrador ativo.
Não recebem destinatário livre: validam e copiam o endereço atual do colaborador,
sem consulta DNS. Bloqueiam participação inexistente, colaborador/treinamento
inativo, endereço inválido e participação concluída. Não existe bypass de
conclusão nesta etapa.

Uma Session própria e FOR UPDATE na participação serializam as solicitações.
A sessão externa do chamador não é commitada ou revertida pelo serviço.
Uma confirmação repetida retorna a solicitação existente, sem novo registro ou
auditoria; a confirmação fica vinculada à mesma participação, ação e solicitante.
UUID já usado com outra combinação é rejeitado. O UUID é global por solicitação;
um futuro lote precisará de UUIDs diferentes por participação, e não um UUID
compartilhado entre todas as linhas.

Outro pedido de primeiro envio, mesmo com UUID diferente, retorna a sequência 1
existente. Não cria um reenvio implícito. Essa leitura idempotente não reativa o
envio, inclusive quando a participação se torna inelegível posteriormente.
A elegibilidade é verificada antes de novas solicitações e novamente na reserva.

Reenvio exige primeiro envio existente e última solicitação terminal. Gera
MAX(sequência)+1 sob lock da participação. Estados pendente/em_processamento
bloqueiam nova confirmação de reenvio. Duas confirmações iguais concorrentes
retornam a mesma linha; duas diferentes concorrentes criam apenas um novo
pendente e a outra recebe erro de conflito. Após enviado, falha, cancelado ou
resultado_indeterminado, a ação explícita de reenvio permite nova sequência.
O chamador futuro deverá tornar o risco de reenvio de resultado indeterminado
visível na confirmação humana; não há retry automático.

Solicitação e Auditoria são commitadas juntas. A tabela de auditoria recebe
usuário, ação, identificação do envio e detalhes contendo somente IDs/sequência.
Eventos humanos: convite_primeiro_envio_solicitado,
convite_reenvio_solicitado e convite_cancelado.

## Cancelamento, reserva e transporte

`cancelar_envio` é idempotente para cancelado e permitido somente enquanto
pendente. Cancelar em processamento é proibido: a conexão externa futura pode
ter iniciado e já não é possível assegurar ausência de envio.

`reservar_proximo_envio` escolhe uma linha pendente por ID com
FOR UPDATE SKIP LOCKED e grava estado/tentativa na mesma transação. Revalida
elegibilidade e snapshot do destinatário. Mudança do endereço causa falha
destinatario_alterado, sem transporte. A reserva devolve um DTO sem token,
URL ou conteúdo. Uma falha de elegibilidade é devolvida como registro processado,
para não interromper o consumo de outros itens; None significa fila vazia.

O transporte é chamado por `processar_proximo_envio` após commit e fechamento da
Session da reserva. Não há lock durante transporte. A finalização usa nova
transação curta. Resultado repetido é idempotente; resultado conflitante não
sobrescreve o anterior. A concorrência dessas operações é garantida e testada
em PostgreSQL. SQLite é usado somente nos testes sequenciais de regras.

`TransporteConvites` define a interface; `TransporteFalso` permite sucesso, falha
ou resultado indeterminado controláveis. Não abre rede, não guarda payload e
não escreve logs. Guarda apenas resultado configurado e quantidade de chamadas.
Não existe configuração SMTP ou seleção automática de transporte no startup.

Exceção genérica vira resultado_indeterminado/excecao_transporte; retorno
malformado vira resultado_indeterminado/retorno_invalido. O texto da exceção não
é persistido nem logado. Nenhum estado terminal volta automaticamente à fila.

Se o processo morrer depois da reserva, ou se a gravação do resultado falhar,
o envio permanece em_processamento. Não há recuperação automática nesta etapa:
isso evita reenviar algo potencialmente aceito. O futuro processamento
operacional deverá tratar reconciliação sem presumir falha do transporte.

## Isolamento e privacidade

Os serviços não alteram início, conclusão, cursor, quiz, tempo, heartbeat,
campanha ou feedback. Snapshot de e-mail fica somente no registro do envio;
auditoria humana não repete esse dado. Nenhum token, URL completa, corpo,
senha ou segredo de transporte é armazenado nos novos registros. O contrato
de transporte da 18.6B recebe somente IDs, destinatário e UUID de correlação.

## Testes

`tests/test_convites_treinamento.py` testa regras em SQLite e PostgreSQL,
concorrência em PostgreSQL, constraints SQL e upgrade/downgrade/reupgrade em
schema isolado. Compare_metadata verifica correspondência da migration com os
dois novos modelos. O teste de migration do feedback foi atualizado apenas
para reconhecer o novo head e continuar verificando a revisão específica
c185b7e4a902; seu teste de imutabilidade continua ativo.

Executar a regressão com ONE_PROGRESSO_TEST_DATABASE_URL e
ONE_HEARTBEAT_TEST_DATABASE_URL apontando exclusivamente a PostgreSQL de testes:

    python -B -m unittest discover -s tests -v
    node --test tests/test_portal_treinamento_heartbeat_browser.cjs
