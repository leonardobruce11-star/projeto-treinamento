# Etapa 18.3D — estabilidade e apresentação do progresso

Proposta baseada no checkout real /opt/One_treino/app, com as etapas 18.3B e 18.3C aplicadas. Preparada em 2026-10-01, sem aplicação ao projeto.

## Ações existentes e política

A administração possui criação de módulo e edição de título, conteúdo e ordem. Não há rota de exclusão, tela de reordenação separada ou ação de transferência de módulo. Há edição dos metadados do treinamento e questões, preservada nesta proposta.

A estrutura fica protegida se qualquer participação existente daquele treinamento tiver iniciado_em, ultimo_modulo_id ou concluido_em preenchido. Os dois últimos são sinais conservadores para registros legados sem a data de início. A mera existência de uma participação não iniciada não bloqueia. Tempo ativo e heartbeat não fazem parte dessa decisão.

Antes desse marco, criar, remover e reordenar módulos permanece permitido. Depois dele, adição, remoção e alteração de ordem ficam bloqueadas, inclusive para treinamentos com participações concluídas. Título e conteúdo permanecem editáveis mantendo a sequência. Não existe override administrativo silencioso nesta etapa. Uma mudança estrutural posterior exige uma decisão explícita de versionamento/migração de percurso fora deste patch.

A proteção também impede troca de treinamento e alteração do ID de módulo utilizado, pois esses atributos definem a sequência canônica (ordem, ID). Não foram criadas interfaces para essas operações.

## Proteção server-side e concorrência

app/utils/estrutura_treinamento.py registra um listener before_flush de Session, carregado na inicialização dos modelos. Agrupa os treinamentos afetados e protege criações, mudanças estruturais, session.delete e remoções do relacionamento com delete-orphan. A checagem usa EXISTS sobre participações, sem carregar uma lista por módulo. A rejeição ocorre antes da escrita e as rotas administrativas fazem rollback, exibindo uma mensagem clara.

A validação visual prévia da criação e o campo de ordem readonly servem à apresentação. O listener rejeita também POSTs forjados e alterações ORM, independentemente do formulário. Não há nova rota de exclusão; os testes de remoção exercitam diretamente a camada ORM.

Alterações estruturais adquirem FOR UPDATE na linha do treinamento, em ordem crescente de ID quando houver mais de um. POST de início e avanço adquirem FOR KEY SHARE nessa mesma linha antes de ler os módulos. O bloqueio compartilhado permite operações de participantes em paralelo e conflita com a alteração estrutural. O bloqueio da participação da 18.3B permanece no avanço. Com PostgreSQL no isolamento padrão READ COMMITTED, após esperar a trava a administração revalida se houve início, e o portal lê a estrutura resultante.

Se o início/primeiro avanço vence, a alteração estrutural é bloqueada. Se a alteração vence, o início lê a nova sequência; um POST de avanço enviado com um módulo que deixou de ser o primeiro pendente é rejeitado. As travas duram até commit/rollback.

Essa é uma proteção da aplicação/ORM. SQL direto, operações bulk/Core e ferramentas externas ao ORM não passam pelo listener; não há trigger ou nova migration. As rotas estruturais existentes usam o ORM e são cobertas.

## Apresentação

EstadoProgresso passa a expor quantidade_concluidos, percentual_concluido e status_modulo. O percentual é a quantidade do prefixo concluído dividida pelo total, mantendo o arredondamento inteiro por truncamento. Sequência vazia resulta em 0%, sem divisão por zero e sem implicar conclusão.

A página inicial mostra contagem, percentual e os estados de cada módulo. A página de módulo mantém a posição da página apenas como contexto de leitura; sua barra usa a contagem real concluída. Revisar o módulo 1 após concluir 3 de 5 preserva 60%. O resumo distingue concluído, concluído em revisão, próximo pendente e bloqueado. Só módulos liberados possuem links no resumo. A revisão usa navegação GET; o módulo pendente continua exigindo POST para avançar. As regras de autorização server-side da 18.3B/18.3C continuam válidas.

Na edição administrativa, a ordem protegida aparece readonly com uma explicação. Tentativas de criar ou reordenar recebem mensagem de bloqueio. Título e conteúdo seguem disponíveis.

## Arquivos previstos

- app/utils/progresso_treinamento.py
- novo app/utils/estrutura_treinamento.py
- app/models/__init__.py (registro do listener, sem alteração de colunas/modelos)
- app/portal_treinamento/routes.py
- app/treinamentos/routes.py
- app/templates/portal_treinamento/inicio.html
- app/templates/portal_treinamento/modulo.html
- app/templates/treinamentos/editar_modulo.html
- novo tests/test_treinamento_estabilidade.py
- tests/test_tempo_ativo_exibicao.py (somente contexto da fixture de renderização)
- Este documento.

O teste legado que renderiza modulo.html diretamente passa a fornecer estado_progresso e 0% para a primeira página ainda não concluída. Suas verificações de tempo ativo, heartbeat e submit foram preservadas. Não foi enfraquecida uma regra funcional para acomodar o teste.

## Escopo preservado

Sem migrations, alterações de colunas, heartbeat, coleta/exibição de tempo ativo, cálculo do quiz, nota, campanhas ou etapa 18.3E. Quiz, validação de respostas e conclusão da 18.3C permanecem intactos. O cursor e suas regras de avanço não foram redefinidos; os POSTs de início/avanço receberam apenas a coordenação transacional com a estrutura.

## Validação

Testes isolados em SQLite e PostgreSQL 16 real. Cobrem percentual real, revisão, exemplo 3/5, zero inicial, último concluído, um módulo, sequência vazia, ordens duplicadas por ID, estados visuais, ausência de links futuros, edição de conteúdo, bloqueios de adição/reordenação/exclusão, remoção de órfão, movimento/ID, sinais legados e permissões antes do início.

Corridas PostgreSQL exercitam ambos os vencedores para criação versus início e criação versus primeiro avanço. Regressão integral da 18.2, 18.3B e 18.3C também executada. Templates afetados compilados nos testes.

Comandos: python -B -m unittest discover -s tests -p 'test_*.py' -v; node --test tests/test_portal_treinamento_heartbeat_browser.cjs. ONE_PROGRESSO_TEST_DATABASE_URL e ONE_HEARTBEAT_TEST_DATABASE_URL devem apontar somente a um PostgreSQL exclusivo de testes; as fixtures criam schemas isolados. Sem a primeira variável, testes PostgreSQL de progresso/quiz/estabilidade são ignorados; heartbeat exige a segunda variável.

Servidor PostgreSQL temporário criado em /tmp e encerrado ao final, sem instalações ou escrita no banco da aplicação. A validação do patch contra o checkout é somente dry-run.

## Resultado final

198 testes passaram, sem falhas ou skips: 48 novos (22 SQLite e 26 PostgreSQL), 37 da 18.3B, 51 da 18.3C e 62 da 18.2. A execução Python teve 189 testes em 83,742 s, e Node teve 9 testes. Os quatro cenários de concorrência da 18.3D passaram em PostgreSQL real.

A primeira execução integrada identificou a fixture da 18.2 que renderizava o template diretamente sem estado_progresso; ela foi atualizada conforme descrito acima. A execução final passou integralmente, preservando as verificações de tempo ativo e heartbeat. Logs: validation-python.log e validation-browser.log na pasta temporária da proposta.
