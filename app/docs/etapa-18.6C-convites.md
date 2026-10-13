# Etapa 18.6C — configuração, mensagem e transporte

Proposta sem alteração de schema: head d186b7e4a903 preservado. Sem endpoint,
worker operacional, agendamento, retry ou implementação da 18.6D.

## Configuração

create_app lê a configuração após load_dotenv e a guarda em
app.extensions['mail_convites_config']. Não cria cliente SMTP no startup.
MAIL_ENABLED=false e MAIL_BACKEND=fake são os padrões em todos os ambientes,
inclusive DEV. Selecionar SMTP exige BASE_URL, MAIL_FROM_ADDRESS e SMTP_HOST,
mesmo se desabilitado: erros listam apenas nomes de variáveis ausentes.
SMTP_AUTH_MODE=password exige SMTP_USERNAME e SMTP_PASSWORD.

| Variável | Padrão / contrato |
| --- | --- |
| BASE_URL | vazio; obrigatório para compor; origem confiável definida pelo operador |
| MAIL_ENABLED | false; aceita true/false |
| MAIL_BACKEND | fake; aceita fake/smtp |
| MAIL_FROM_ADDRESS | vazio; endereço simples validado, sem DNS |
| MAIL_FROM_NAME | ONE — Treinamentos |
| MAIL_REPLY_TO | vazio; opcional, endereço validado |
| SMTP_HOST | vazio; hostname sem esquema/path/porta |
| SMTP_PORT | 587 starttls / 465 tls; 1..65535 |
| SMTP_SECURITY | starttls; aceita starttls/tls, nunca texto aberto |
| SMTP_AUTH_MODE | none; aceita none/password |
| SMTP_USERNAME | vazio |
| SMTP_PASSWORD | vazio; sem representação/log |
| MAIL_TIMEOUT_SECONDS | 10; número finito positivo |
| MAIL_BATCH_SIZE | 20; inteiro positivo, reservado para etapa operacional |
| MAIL_RATE_LIMIT | 60; inteiro positivo em mensagens/minuto, reservado para etapa operacional |
| MAIL_TEST_RECIPIENTS | vazio; allowlist opcional separada por vírgulas |

Lote e rate limit são somente configuração nesta subetapa; não há consumidor
operacional. Não alterar .env nem fornecer credenciais reais para os testes.

## URL e composição

BASE_URL aceita http/https, hostname DNS ou IP e porta válida; rejeita controles,
espaços, backslash, percent encoding, credenciais, query/fragmento (inclusive vazios)
e path além de /. A confiança vem da configuração server-side do operador,
nunca de headers, Host, Referer ou formulário. Exemplo:
https://treinamentos.oneinteligencia.com.br

construir_link usa BASE_URL.rstrip('/') + '/aprender/t/' + quote(token, safe='').
O token original é preservado ao decodificar o segmento, sem criar outro token
no reenvio. Tokens vazios, controles e segmentos . ou .. são rejeitados.

A mensagem é somente text/plain UTF-8, com nome, título, link individual,
identificação ONE, orientação para não compartilhar e aviso de que não pede
credenciais. Assunto fixo; nome e título ficam somente no corpo. Campos
interpolados e endereços rejeitam CRLF/controles. Message-ID usa UUID já existente,
sem token. Mensagem e link existem somente em memória; não são logados/persistidos.

## Transporte e bloqueios

TransporteConfigurado mantém o contrato DadosEnvio da 18.6B. Lê participação,
nome, título e token existentes em sessão curta; compõe depois da reserva, sem
conexão/lock SQL durante o transporte. Fake descarta a mensagem e conta chamadas,
sem sockets, logs ou armazenamento do conteúdo. A interface anterior permanece
compatível com testes da 18.6B.

processar_convite_configurado verifica MAIL_ENABLED e a trava SMTP antes de
reservar: desligado ou SMTP real bloqueado deixa a solicitação pendente.
A interface legada processar_proximo_envio(transporte) continua sendo primitiva
para testes; futuros consumidores devem usar a entrada configurada.

AdaptadorSMTP só funciona com fabrica_simulada injetada explicitamente pelos
testes. Sem ela, bloqueia smtp_real_bloqueado_18_6c mesmo com MAIL_ENABLED=true.
A liberação de smtplib.SMTP/SMTP_SSL real exige mudança futura deliberada.
O contrato simulado usa context manager, timeout, EHLO, STARTTLS + novo EHLO
ou TLS direto, ssl.create_default_context com certificado/hostname verificados,
login somente em password e send_message com envelope explícito. Sem debug.
Erros SMTP/OS, inclusive timeout, viram resultado_indeterminado/excecao_transporte,
sem exceção bruta, logs ou retry automático.

Allowlist é aplicada em qualquer ambiente quando configurada, incluindo fake:
nunca troca destinatário. Bloqueio registra somente mail_destinatario_bloqueado.
No pipeline persistente usa falha/destinatario_invalido, código já permitido
pela constraint da 18.6B; não amplia códigos nem altera migration anterior.

## Validação

Executar unittest discover com ONE_PROGRESSO_TEST_DATABASE_URL e
ONE_HEARTBEAT_TEST_DATABASE_URL apontando exclusivamente para banco de testes;
executar também node --test tests/test_portal_treinamento_heartbeat_browser.cjs.
Os testes SMTP usam MagicMock, sem conexão SMTP local ou externa.
