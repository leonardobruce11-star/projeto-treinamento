# Etapa 18.2B — endpoint de heartbeat

`POST /aprender/t/<token>/modulo/<int:modulo_id>/heartbeat`

Corpo JSON: `{"segundos": 15}`. Enviar o token CSRF da sessão do portal no
cabeçalho `X-CSRFToken`. Não há isenção de CSRF. Não é necessário login interno:
a participação é obtida exclusivamente pelo token da URL, com treinamento ativo.
O módulo deve ter o ID da URL e pertencer ao treinamento dessa participação.
Campos extras como `participacao_id` e `modulo_id` no corpo não determinam a identidade.

## Regra temporal

Somente inteiros de 1 a 30 são aceitos; booleanos, strings, decimais e valores
fora dessa faixa retornam HTTP 400, sem alterar o agregado ou sua referência.
O intervalo de referência para futura integração é 15 segundos. Esta etapa
não implementa nenhum temporizador ou chamada no navegador.

T é `statement_timestamp()` do PostgreSQL, um instante com fuso horário,
comparado como tempo absoluto (UTC). U é `ultima_atividade_em` da linha atual.
S é o valor enviado pelo cliente:

```text
referência_nova = max(T, U)
tempo_decorrido = floor((referência_nova - U) em segundos)
incremento_efetivo = min(S, 30, tempo_decorrido)
```

O primeiro heartbeat cria o agregado com zero segundos e estabelece a referência
com o horário do banco. Quando um agregado existente tem U nulo, apenas estabelece
a referência, preservando qualquer total anterior; não credita tempo sem referência.

Cada requisição válida atualiza a referência, inclusive quando seu incremento
é zero. Frações são descartadas e não são transportadas para a próxima requisição:
chamadas excessivamente frequentes podem subcontabilizar tempo, mas não inflá-lo.
Uma referência futura não retrocede, e produz incremento zero até o relógio alcançá-la.
Não há recuperação de todo o intervalo perdido: após 90 segundos, uma requisição
pode acrescentar no máximo 30 segundos. O tempo também nunca excede S.

Exemplos com S=30: primeiro heartbeat soma 0; após 15 segundos soma 15;
repetição antes de um segundo soma 0; após quatro segundos soma 4;
após 90 segundos soma 30.

O upsert usa `INSERT ... ON CONFLICT DO UPDATE` com a restrição única existente.
A fórmula é avaliada dentro do UPDATE, usando a linha atual sob o bloqueio do
PostgreSQL. Um `SELECT ... FOR UPDATE` da participação serializa as requisições
da mesma participação, inclusive a criação simultânea, sem modificar seus campos.
Sob esse bloqueio, o total anterior é lido apenas para calcular o delta retornado.
O cálculo do incremento e sua soma permanecem no upsert atômico. O timestamp do
upsert é obtido depois da aquisição do bloqueio; não usa o início da transação.
O bloqueio cobre módulos diferentes da mesma participação, com transações curtas.

`ultima_atividade_em` e `atualizado_em` são atualizados com horário do banco e
não retrocedem; `criado_em` é preservado em registros existentes. O commit precede
a resposta. Falhas SQL retornam JSON HTTP 500 com rollback, sem expor SQL ou tokens.

## Respostas

HTTP 200, inclusive incremento zero:

```json
{"sucesso": true, "incremento_efetivo": 15, "segundos_ativos": 15}
```

Erros do endpoint usam `{"sucesso": false, "erro": "..."}`:

- HTTP 400: `incremento_invalido` ou `csrf_invalido`.
- HTTP 404: `participacao_indisponivel` ou `modulo_invalido`.
- HTTP 500: `falha_ao_registrar`.

A rota aceita POST (e OPTIONS automático do Flask). Outros métodos retornam o
HTTP 405 padrão do Flask. URLs que não correspondem à rota retornam o 404 padrão.
O handler CSRF preserva o HTML padrão dos erros das demais rotas do portal.

Esta proteção limita tempo por intervalo real; ela não comprova presença humana.
Visibilidade de aba, foco e inatividade ficam fora desta etapa. Não há alterações
em início, progresso, conclusão, quiz, `ultimo_modulo_id`, templates ou dashboards.

## Testes

Usar PostgreSQL real e uma URL explícita de um banco de testes dedicado, com
permissão de criar schema. Não usar a URL de produção. A suite não carrega `.env`
nem chama a factory de produção. Cria um schema exclusivo com UUID, usa os modelos
reais da plataforma, e remove o schema ao terminar.

```bash
ONE_HEARTBEAT_TEST_DATABASE_URL='postgresql+psycopg://usuario@localhost/one_heartbeat_test' \
  .venv/bin/python -m unittest discover -s tests -p 'test_portal_treinamento_heartbeat.py' -v
```

Abrange registro da rota, métodos, CSRF, criação, incremento, tipos e limites,
intervalos longos, descarte de frações, repetição, referência futura ou nula,
validação de token e treinamento ativo, módulo inexistente ou de outro treinamento,
identidade derivada do token, preservação de progresso/quiz e criação/atualização
concorrentes com seis clientes e conexões reais.
