# Etapa 18.7B — configuração segura e aprovação local

Proposta para revisão, preparada em cópia isolada do estado real. Não aplicada.
Não cria campanhas externas, sincroniza HTML real, importa resultados, dispara
mensagens, habilita SMTP ou implementa a preparação externa da 18.7C.
Nenhuma migration e nenhuma alteração ao mecanismo de convites da 18.6.

## Configuração

Valores seguros sugeridos (não é necessário editar o .env para obter os padrões):

    GOPHISH_ENABLED=false
    GOPHISH_SEND_ENABLED=false
    GOPHISH_BACKEND=fake
    GOPHISH_URL=
    GOPHISH_API_KEY=
    GOPHISH_TIMEOUT_SECONDS=5
    GOPHISH_CA_BUNDLE=

Configuração carregada no startup, após load_dotenv, e guardada nas extensions
Flask; serviço fake conserva somente IDs/flags em memória por aplicação.
Backend http exige origem HTTPS, sem credenciais, path, query, fragmento,
controles, percent-encoding ou barra invertida, além de chave não vazia.
Timeout finito, maior que zero e no máximo 60 segundos. Booleanos aceitam
somente true/false (ignorando caixa e espaços externos). Erro bloqueia operações.
Integração desabilitada não abre conexão, mesmo que URL/chave estejam presentes.
O fake não necessita URL/chave, nunca instancia HTTP/socket/SMTP e não conserva
HTML, destinatários ou payloads. Não é armazenamento persistente.

Envio é false incondicionalmente nesta etapa, inclusive se configurado true.
executar_campanha apenas retorna disabled/forbidden; não possui endpoint externo.
Não há método para criar campanha externa. Mutações HTTP de grupos/templates/
páginas também retornam forbidden, antes de qualquer HTTP, até existir contexto
aprovado na 18.7C. Os mesmos métodos de recursos funcionam no fake para testes.

## TLS e HTTP

Remove verify=False. O transporte usa o cafile padrão do sistema identificado
por ssl.get_default_verify_paths(), ou GOPHISH_CA_BUNDLE absoluto e existente.
CA inexistente/relativa bloqueia configuração; CA/certificado inválido produz
tls_error. Sem cafile de sistema disponível, falha fechada em tls_error.
Nenhuma opção permite desativar verificação. O conteúdo PEM é validado pelo TLS
quando utilizado; a existência do arquivo não declara validade do certificado.

Session.trust_env=False impede proxy/CA herdados de requests; redirects
HTTP estão desativados. Não há retry automático, nem fallback para HTTP.
Consultas reais só ocorrem por chamada explícita do cliente habilitado http;
nesta etapa a página administrativa e /health mostram apenas configuração local.

## Contrato de respostas

Métodos de recurso retornam sempre ok, status, data e http_status.
Sucesso GET exige JSON e ID inteiro positivo; listas exigem lista de objetos com
IDs válidos. Consulta/PUT exige ID correspondente ao solicitado. Página exige
flags booleanos explícitos. DTO permite somente IDs e os flags necessários;
corpo, HTML, headers, chave, destinatários e resultados individuais não retornam.
Os métodos de campanha são apenas consultas compatíveis, com o mesmo DTO mínimo;
não importam resultados. verificar_conexao retorna status, backend e send_enabled.

Estados: disabled, not_configured, not_found (HTTP 404), auth_error (401),
forbidden (403 ou operação bloqueada), timeout, tls_error, server_error (5xx),
invalid_response (JSON/shape/ID/HTTP inesperado), connection_error e
result_indeterminate (timeout/conexão incerta de mutação simulada).
Invalid_request e invalid_content identificam falha de ID e política de conteúdo.

Não retornam detail, response.text, exception string ou headers. Não há logging
no cliente/transporte. Repr da configuração oculta chave, URL e caminho de CA.
Nenhuma classificação tenta recriar recurso. 404 de consulta é ausência confirmada;
outros erros não são ausência. Resultados indeterminados nunca geram retry.

## Governança e permissões

Solicitar aprovação independe dos três IDs externos. Valida grupo/treinamento
locais ativos; para simulação valida também cenário/template/landing ativos,
compatibilidade template/cenário, participante ativo e contrato de conteúdo.
Aprovação revalida os recursos locais. Administrador ativo diferente do criador
continua obrigatório. Rascunho -> aguardando_aprovacao -> aprovada não chama API.
Preparação de simulação permanece indisponível até a 18.7C; campanhas normais
preservam preparação/piloto educacional sem consultar Gophish.
Import de Treinamento corrigido minimamente nas rotas, coberto por teste real
de GET/POST de criação e GET de edição de campanha.

Política reutiliza o decorador administrador existente em app/convites/routes.py:
usuário autenticado, ativo e perfil administrador, com CSRF global nos POSTs.
Não há RBAC novo, permissões por departamento ou autorização multiempresa.
Essas limitações e a dependência do decorador existente ficam explícitas.

POSTs avulsos de grupos/templates/páginas/grupo-teste ficam indisponíveis,
mesmo com administrador e integração habilitada. Não possuem contexto/versionamento
de campanha aprovada; retornar 409 evita exportação antes da governança.
Não autenticado segue login; operador/inativo recebe 403. Integração desabilitada
continua sem transporte. Formulários existentes têm botão desabilitado e aviso.
GET administrativo exige administrador ativo, não consulta API e não mostra segredos.
IDs externos legados continuam no banco, sem alteração ou recriação.

## Saúde e conteúdo

/health: 200/status ok se SELECT 1 funciona; 503/status error se banco falha.
Campo gophish é diagnóstico local opcional (disabled, not_configured ou ready),
backend e send_enabled=false; ready não significa servidor conectado.
Indisponibilidade de Gophish nunca determina saúde principal nem causa consulta HTTP.

Validação HTML conservadora, sem renderização: tags permitidas, sem formulários,
inputs, textarea, scripts (inclusive inline), iframes, assets externos, estilos,
handlers on*, redirects meta ou links externos. Links relativos locais,
fragmentos e o marcador Gophish de simulação são permitidos. Flags de captura
precisam ser exatamente false. Rejeita termos/UI de login/autenticação, inclusive
entidades e texto dividido em tags; revisão humana continua necessária.
Essa política também rejeita alguns textos educativos que mencionem termos de
credenciais. A 18.7C deverá usar conteúdo estruturado e revisão explícita; não
há pretensão de detectar toda imitação semântica nem sanitizar HTML arbitrário.
Não sincroniza conteúdo real nesta etapa e não cria landing pública nova.

## Validação e aplicação

Testes novos: tests/test_gophish_seguro.py. Flask real com SQLite em memória,
load_dotenv mockado, configuração sintética, sockets/HTTP bloqueados e CSRF ativo.
HTTP é substituído por mocks, inclusive casos TLS, 404/401/403/500, JSON/ID inválido.
Testa fake, contrato mínimo, ausência de segredos em logs/respostas, permissões,
health independente, aprovação local, autoaprovação bloqueada e normal sem Gophish.
Regressão existente é executada com SMTP/HTTP/TCP bloqueados, mantendo testes
PostgreSQL somente via URLs explícitas de banco exclusivo de testes.

Resultados e limitação PostgreSQL: consultar o relatório de revisão entregue
junto do patch. Não considerar testes PostgreSQL pulados como aprovados.
Não carregar DATABASE_URL/.env de produção para executar testes.

Aplicação somente após confirmação do usuário e verificação dos hashes dos
arquivos originais do patch. Nenhuma edição automática de .env, migration,
reinício, daemon, Gophish, worker ou transporte de e-mail faz parte desta proposta.
