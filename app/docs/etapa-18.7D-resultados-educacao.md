# Etapa 18.7D — proposta para revisão

Implementação preparada em cópia isolada. Não aplicada à ONE. Nenhuma campanha externa, SMTP, envio, resultado real ou serviço Gophish foi executado. Migration proposta: `a187d7e4a906`, imediatamente após `f187c7e4a905`.

## Arquitetura e fronteiras

A ONE permanece responsável pelo cadastro, público autorizado, aprovação, revisão imutável, execuções, educação, participações e auditoria. O mecanismo externo registra somente sinais técnicos. Nenhum evento inicia ou conclui treinamento, marca módulos, gera quiz, altera feedback ou contabiliza tempo.

Fluxo futuro: revisão aprovada → execução/público congelado → preparação auxiliar da 18.7C → vínculo externo confirmado por consulta administrativa → resultados normalizados → landing ONE dedicada → ação explícita → autorização de portal vinculada à sessão → fluxo educacional existente.

Esta entrega não cria campanhas externas, sending profiles nem agendamentos. O adapter de resultados aceita **somente fake**. Backend HTTP recebe `forbidden`, inclusive se estiver configurado e habilitado. Isso evita presumir compatibilidade com campos que ainda não foram homologados na API real. A preparação auxiliar existente da 18.7C continua respeitando configuração, ambiente, aprovação e validações conservadoras. A integração permanece desabilitada por padrão e envio bloqueado. Nenhum ID legado é promovido ao novo vínculo.

## Modelos e constraints

| Modelo | Responsabilidade e restrições |
| --- | --- |
| `VinculoCampanhaSimulacao` | Uma linha por execução; revisão/instância coerentes por FK composta; nome `ONE-CAMP-{uuid_execucao_sem_hifens}-{digest_revisao}`; ID externo positivo ou nulo; UNIQUE instância/ID e instância/nome; vínculo confirmado não pode trocar de ID; estados pendente, vinculado, falha, resultado_indeterminado, cancelado. |
| `EventoSimulacao` | FKs compostas garantem execução/instância e alvo/execução/RID; quatro tipos permitidos; timestamps UTC de origem/importação; chave SHA-256 UNIQUE; ID estável opcional e discriminador inteiro seguro; metadados fixos `contrato=1`; eventos imutáveis no ORM e em PostgreSQL. |
| `AssociacaoEducacionalSimulacao` | Um vínculo explícito posterior por alvo; referência à participação e administrador; imutável. Trigger exige colaborador, treinamento e campanha correspondentes e participação original do snapshot nula. |
| `PonteEducativaSimulacao` | UUID4 dedicado, alvo/responsável, validade, consumo e revogação; CHECK estados e expiração; índice parcial UNIQUE impede duas pontes ativas/consumidas por alvo. A ponte anterior deve ser revogada antes de substituição. |
| `LeaseOperacaoSimulacao` | Uma linha por operação; geração bigint monotônica, executor UUID, responsável, prazo e estado; CHECK distingue lease legado sem dono de lease ativo/encerrado identificado. |

As tabelas da 18.7C permanecem. Acrescentam-se unicidades compostas de execução/instância e alvo/execução/RID para as novas FKs. `OperacaoSimulacao` passa a aceitar ações vincular/importar e estado cancelada. FKs usam RESTRICT, sem exclusão em cascata do histórico. Índices apoiam consultas por execução, origem e alvo.

PostgreSQL instala triggers de imutabilidade, consistência educacional, vínculo e fencing. O marcador transacional contém geração e executor, sem dados de participantes. Um marcador antigo não pode atualizar a operação, recursos, execução, vínculo ou identidade do alvo depois da tomada de posse; eventos exigem operação de importação. O último lease continua protegendo os registros após finalização.

## Vínculo externo e identidade

Um administrador ativo informa o ID externo em uma ação com confirmação assinada, vinculada ao ator, escopo e prazo. A ação apenas consulta o fake. Nome técnico, digest e instância devem corresponder exatamente. O ID somente é persistido após validação; vínculo conhecido pode ser reconciliado como histórico sem exigir que cadastros atuais reproduzam o snapshot antigo. Novo vínculo exige revisão vigente e configuração aprovada íntegra.

Resultados usam RID dedicado. E-mail serve exclusivamente à associação inicial dentro do público congelado daquela execução; depois disso, RID é fixo. Não se consulta identidade global por e-mail. Cadastros alterados não substituem endereços aprovados. Dois RIDs para o mesmo alvo, RID repetido na janela, e-mail fora do snapshot, RID divergente ou evento sem alvo autorizado rejeitam toda a janela e fazem rollback das associações/eventos. Tokens de participação e API key são rejeitados como identificadores; tokens de tamanho seguro também são rejeitados se embutidos em um identificador. Nunca são exportados pelo serviço.

Contrato fake de campanha: ID positivo, nome técnico, digest, lista de resultados `{rid, email?}` e timeline. Campos extras dos resultados/eventos não são projetados. Fixtures podem semear esse contrato em memória; essa função não representa POST/criação de campanha.

## Eventos, deduplicação e janela

Normalizações permitidas: `Email Sent` → mensagem_enviada; `Email Opened` → mensagem_aberta; `Clicked Link` → link_acessado; `Error Sending Email` → erro_envio. Também são aceitos os quatro nomes normalizados.

Mensagem enviada significa apenas reporte pelo mecanismo, sem confirmação de entrega. Abertura e acesso podem resultar de scanners, proxies e ferramentas automáticas; não constituem prova de intenção humana. A interface apresenta essas limitações e separa progresso educacional.

Eventos desconhecidos/submissão de dados são descartados **antes** de ler outros campos. Campos digitados, senha, cookies, details, corpo HTTP e payload bruto nunca entram na persistência/auditoria. Eventos conhecidos com identidade, data ou discriminador inválidos são descartados sanitizadamente. Envelope inválido e inconsistências de identidade rejeitam a janela. Auditoria registra apenas contagens/códigos e IDs internos.

Deduplicação prioritária: SHA-256 da instância, UUID da execução e ID estável do evento. Sem ID: instância, UUID da execução, RID, tipo, timestamp original normalizado em UTC e sequência inteira opcional. Datas com offsets equivalentes convergem. Colisão de ID estável com alvo/tipo/data diferentes falha fechada.

Reprocessa-se todo histórico disponível na consulta, até 10.000 resultados e 20.000 eventos; não há cursor de data como fonte de verdade. Eventos atrasados e fora de ordem são aceitos, e `UNIQUE` + `ON CONFLICT DO NOTHING` protegem repetição e concorrência. IDs de operação são idempotentes; processamento por execução é serializado com locks e uma operação ativa. Descarta-se por contagem da **última janela**, sem acumular reimportações como se fossem descartes novos.

Limitação do fallback: duas ocorrências com mesmo RID/tipo/instante e sem ID/discriminador são indistinguíveis e colapsam. O adapter real deverá fornecer identidade/discriminador estáveis quando disponíveis, validar escopo do ID e implementar paginação/limites antes de uso real. O histórico completo é preferível nesta etapa; janelas sobrepostas futuras precisam preservar a mesma UNIQUE.

## Ponte educativa e portal

Rota pública `/simulacao/educacao/<uuid>`: conteúdo ONE sobre remetente, urgência e links, declaração de simulação interna autorizada, boas práticas e indicação de continuidade. Não contém nome, e-mail, departamento, resultados de outros participantes, pontuação, login ou scripts externos. Há somente um POST com CSRF e botão **Acessar treinamento**, quando existe participação válida.

GET não consome a ponte, não abre o treinamento e não registra progresso. POST consome a ponte uma vez e cria um alias aleatório `one-session-...`, autorizado pela sessão assinada do navegador por até 30 minutos e nunca além do prazo da ponte. A URL/HTML/Location usa somente esse alias: não usa o token real da participação. Replay do POST é permitido apenas ao mesmo navegador, com grant válido; outro navegador não pode usar o alias. Revogação/expiração bloqueiam também o portal por alias.

A ponte tem validade padrão 24 horas; criação administrativa aceita 1–168 horas e requer confirmação. Vínculo educacional original é preservado. Se ausente, serviço administrativo explícito pode criar uma participação local usando o mesmo mecanismo de token aleatório do sistema ou associar uma participação já existente com campanha/treinamento/colaborador exatos. Criação não envia convite, não inicia treinamento e é idempotente por alvo. Participação já existente exige associação explícita, sem escolha automática. Não se altera o snapshot para acrescentá-la.

Validação ocorre a cada acesso: revisão aprovada íntegra, alvo autorizado, execução não cancelada, colaborador ativo, ponte válida e participação/treinamento correspondentes ativos. Desabilitar Gophish não interrompe uma ponte educativa local válida. O bearer dedicado pode ser usado por quem o possuir; não equivale à comprovação da identidade da pessoa. A organização deve decidir o canal de entrega e a necessidade de autenticação adicional antes do uso real, sem converter a landing em formulário de credenciais.

A adaptação do portal troca apenas a resolução/autorização e propagação do identificador de acesso. Convites normais continuam usando seus tokens e rotas existentes. O POST **Iniciar treinamento** e as ações de progresso existentes continuam responsáveis pelo início/progresso real. Alias é prefixo reservado; tokens normais não devem usar esse prefixo.

Headers de todos os retornos da landing, incluindo erro/CSRF: `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, CSP default-src none, script-src none, form-action self, frame-ancestors none, base-uri none, style/img self; nosniff. Portal por alias também recebe no-store/no-referrer. `log_privacidade.py` redige identificadores educativos, inclusive percent-encoding, aliases/tokens do portal e e-mails; exceções e pipeline de logs recebem a mesma proteção. A proteção do proxy/servidor já estabelecida na 18.6E-1 deve ser verificada no ambiente de implantação; esta proposta não modifica arquivos de infraestrutura.

## Lease, fencing e recuperação

Reserva: transação curta com locks, validações e criação da operação/lease; commit; consulta/transporte fora de transação; nova transação valida lease e persiste resultado. TTL inicial de 180 segundos, renovado em checkpoints curtos; PostgreSQL usa clock_timestamp do banco. Geração e executor também são verificados na capability antes de POST auxiliar permitido pela 18.7C. Não há worker de envio.

Expiração confere elegibilidade, não prova de abandono. Retomada exige administrador ativo e confirmação explícita. Lease não expirado não pode ser recuperado por simples idade da operação. Tomada de posse incrementa exatamente uma geração, troca executor e audita. Operação legada processando recebe generation 0 e exige confirmação adicional de encerramento do executor antigo. A interface deixa essa declaração explícita.

Executores retomados fazem **somente leitura**: preparam tarefas sem payload para recursos auxiliares ou repetem consulta/importação técnica. Novo executor não repete POST auxiliar. Timeout de POST permanece indeterminado; consulta por nome/digest correto pode estabelecer vínculo, mas lista vazia sem comprovação não libera recriação. Timeout nunca vira 404. Respostas 401/403/500/TLS/JSON inválido têm classificações distintas e não provocam criação.

Cancelamento administrativo é local, confirmado e auditado, com nova geração quando elegível: encerra a operação/execução e revoga pontes. Não apaga histórico, não presume ausência externa nem cancela recursos no Gophish. Lease ativo ainda válido impede cancelamento/tomada de posse. Operações terminais bem-sucedidas não são canceladas por esse caminho.

Fencing impede persistência por executor velho; não consegue cancelar um HTTP já recebido por sistema externo antes da recuperação. Por isso, recuperação de POST incerto é exclusivamente leitura e lançamento real depende de contrato operacional adicional na 18.7E.

## Interface e auditoria

Página existente da simulação, restrita à política administrativa atual (autenticado, ativo, perfil administrador), apresenta revisão/digest/fase/público, recursos, vínculo, contagens por tipo, último evento novo importado, reconciliação, descartes da última janela, última operação, geração/prazo de lease e estado da ponte. Por alvo: ID local, RID, eventos normalizados, participação educacional separada e estado da ponte. Não há métricas individuais nos dashboards gerais.

Ações passam por confirmação assinada com CSRF: consultar vínculo, importar, retomar somente leitura, encerrar localmente, criar/revogar ponte, criar participação local ou associar participação existente. Sem botão de disparo. Não foi criado RBAC novo; a limitação continua sendo perfil administrativo amplo, sem separação de funções além das regras existentes.

Auditoria cobre vínculo local/reconciliado, início da importação, novos eventos/descartes, identidade ambígua, recuperação de geração, cancelamento, associação educacional e criação/revogação de ponte. Registra IDs internos, contagens e códigos; omite bearer, e-mail, API key, tokens e payloads.

## Migration e reversão

Upgrade é aditivo, sem dependências externas: cinco tabelas novas, FKs/UNIQUE/checks/índices e triggers. Operações legadas processando ganham lease de estado legado; não há adoção automática de IDs externos. Códigos antigos não podem finalizar essas operações sem migração explícita de executor.

Downgrade remove as tabelas novas e restaura os checks de ação/estado da 18.7C. É destrutivo para o histórico da 18.7D e requer planejamento/backup. Se existirem operações com ações vincular/importar ou estado cancelada, a restauração dos checks falha atomicamente; não há exclusão silenciosa de operações para viabilizar rollback. Upgrade → downgrade → upgrade é validado em schema PostgreSQL temporário vazio da 18.7D, preservando a base 18.7C. Não executar downgrade em ambiente ativo com dados sem plano específico.

## Retenção proposta, sem exclusão automática

Política interna a aprovar: eventos técnicos por 90 dias após encerramento; vínculos e resumos operacionais por 12 meses; pontes expiram rapidamente e são revogadas no encerramento, com eliminação do bearer após a necessidade operacional; auditoria por prazo próprio aprovado pela governança (sugestão inicial 24 meses). São propostas operacionais, sem afirmar exigências legais.

Participação e histórico educacional seguem a política educacional da ONE, independentemente da retenção de sinais da simulação. Antes de implementar purge, decidir anonimização e ordem de exclusão compatível com FKs/imutabilidade; os triggers atuais bloqueiam exclusão de eventos/histórico e exigirão procedimento controlado específico. Nenhum job ou exclusão foi implementado.

## Pendências obrigatórias antes de execução real / 18.7E

1. Homologar adapter HTTP somente leitura, versão real da API, identidade dos resultados/eventos, paginação e limites. Verificar vínculo/digest sem depender do campo fake `digest` como se fosse nativo da API.
2. Definir provisionamento da campanha externa, aprovação explícita separada de execução, bloqueios DEV/homologação, piloto/ampliada e procedimento de lançamento. Nenhuma dessas ações é liberada por esta entrega.
3. Definir como Gophish encaminhará RID/URL educativa dedicada sem token de treinamento, exposição de bearer, canal de entrega e eventual comprovação adicional de identidade.
4. Validar proteção efetiva dos logs de proxy, servidor, monitoramento e Referer no ambiente, HTTPS/cookies/sessão e prazo adequado para continuidade do treinamento.
5. Definir recuperação de execução externa indeterminada e limites de fencing local em chamadas já em trânsito; jamais repetir disparo por timeout.
6. Aprovar retenção, informação aos participantes, revisão de acesso individual e procedimento de revogação/encerramento. Resolver identidade de eventos indistinguíveis no fallback.

Até essas decisões e uma implementação posterior explicitamente autorizada, a integração continua desabilitada e nenhum envio/campanha executável está disponível.
