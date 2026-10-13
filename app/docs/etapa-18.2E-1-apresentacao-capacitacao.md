# Etapa 18.2E-1 — apresentação gerencial e individual

## Diagnóstico do checkout analisado

Embora a aplicação da 18.2E tenha sido informada, neste workspace a rota do
dashboard não importa/chama calcular_metricas_capacitacao nem envia métricas de
tempo. O template não inclui a seção e os arquivos metricas_capacitacao.py e
 dashboard/tempo_capacitacao.html não existem no projeto. Portanto a seção não
poderia ser renderizada por estes arquivos, independentemente da existência de
registros. Não foi verificado se o ambiente observado serve este mesmo checkout.

O dashboard envia atualmente:
- total_colaboradores, total_colaboradores_ativos, total_departamentos,
  total_treinamentos, total_campanhas;
- campanhas_rascunho, campanhas_aprovadas, campanhas_em_preparacao,
  campanhas_piloto_aprovado, campanhas_reprovadas;
- total_participacoes, total_iniciados, total_concluidos, total_pendentes,
  total_nao_iniciados, total_em_andamento, taxa_conclusao;
- total_quizzes, media_quiz, resultados_recentes, indicadores_departamentos.

O perfil envia colaborador, grupos, participacoes, tempos_ativos,
total_participacoes, total_nao_iniciados, total_em_andamento, total_concluidos e
media_quiz. A tabela já contém tempo por participação, mas não resumo acumulado
nem categoria. O filtro tempo_ativo da 18.2D está registrado na fábrica.

## Arquivos da proposta

- app/dashboard/routes.py: integrar métricas e preservar as três correções
  locais anteriormente aprovadas, ausentes neste checkout.
- app/templates/dashboard/index.html: inclusão imediatamente após o cabeçalho
  e correção da referência do cartão Em andamento.
- app/templates/dashboard/tempo_capacitacao.html: seção gerencial com quatro
  cards, tabelas por treinamento/categoria e estados vazios.
- app/utils/metricas_capacitacao.py: consolidação SQL da 18.2E e contagem
  adicional de colaboradores distintos com tempo positivo.
- app/colaboradores/routes.py: carregar categoria em lote, enviar tempo
  acumulado e contagem de participações iniciadas ao histórico existente.
- app/templates/colaboradores/detalhe.html: ampliar o histórico com resumo e
  coluna Categoria, mantendo campanha/status/progresso/quiz/conclusão/tempo.
- tests/test_capacitacao_apresentacao.py e esta documentação.

Não modifica o utilitário de duração ou os arquivos de coleta, portal, quiz,
campanhas, modelos, migrations e feedback. Não implementa a etapa 18.3.

## Apresentação

Dashboard: Capacitação e tempo de treinamento, antes de Visão do ambiente.
Cards: Horas totais de capacitação, Tempo médio por participação ativa,
Participações com atividade registrada, Colaboradores com atividade de treinamento.
As tabelas por treinamento e categoria seguem as classes existentes de tabelas.
Os quatro cards usam environment-grid/environment-card, responsivos no CSS atual.

Perfil: amplia Histórico de treinamentos para Histórico de capacitação, sem
segunda tabela. Dentro do bloco, mostra Tempo total acumulado, Treinamentos
iniciados e Treinamentos concluídos. A tabela recebe Categoria; sem categoria,
mostra Sem categoria. Participações sem tempo exibem 0 min e quiz ausente mantém —.
Resumo também aparece quando não há participações. Contagens são por participação,
como no histórico atual; repetir o mesmo treinamento representa outra participação.
Iniciadas usam iniciado_em; concluídas usam concluido_em, sem usar datas para duração.

Reutiliza exclusivamente o filtro tempo_ativo da 18.2D: 0 min, 45 s, 12 min 30 s,
1 h 08 min. Mesmo o card Horas totais mostra minutos/segundos quando abaixo de uma
hora, evitando arredondar valores pequenos para zero. Não há novo formatador.

## Fonte, consultas e isolamento

Fonte exclusiva da duração: TempoTreinamento.segundos_ativos.
Resumo global soma módulos por participação antes de calcular SUM/AVG/COUNT.
A média e contagem excluem totais iguais a zero, inclusive primeiro heartbeat.
COUNT(DISTINCT CASE WHEN soma > 0 THEN colaborador_id END) calcula pessoas com
atividade na mesma consulta, sem contar a pessoa novamente a cada participação.

Quatro SELECTs fixos: resumo, totais por treinamento, colaborador e categoria.
Agrupamentos usam IDs/nome, junções externas e COALESCE para zeros.
Sem categoria é um grupo adicional quando existem treinamentos sem categoria.
O utilitário mantém os totais por colaborador da 18.2E disponíveis; nesta interface
seu detalhe aparece no perfil, sem uma terceira tabela gerencial no dashboard.
Histórico de cadastros inativos continua incluído.

No perfil, os totais por participação vêm da única agregação já existente da
18.2D, filtrada pelas participações desse colaborador. O acumulado soma esses
resultados; não executa uma consulta adicional de tempo. Categoria usa selectinload
encadeado com treinamento, sem consulta por linha. Grandes listas podem ser
divididas em lotes pelo SQLAlchemy. Consultas anteriores de departamentos/grupos
não foram reestruturadas nesta subetapa.

## Três correções locais já aprovadas

O retorno do dashboard sai do laço de departamentos; o total departamental usa
outra variável para não sobrescrever o total global; o cartão Em andamento usa
total_em_andamento em lugar da referência inexistente total_em_concluidos.
Não muda as fórmulas de indicadores nem regras de treinamento.

## Validação e aplicação

    python -B -m unittest discover -s tests -p test_capacitacao_apresentacao.py -v

23 testes passaram em SQLite em memória, com foreign keys habilitadas e
renderização StrictUndefined. Cobrem métricas, zeros, ausência de dados,
contagem distinta, categorias, média, histórico único, resumo individual,
quiz somente de leitura, contagens de início/conclusão, isolamento entre pessoas,
consultas constantes, ausência de writes, acesso e 404. As consultas também
compilam para PostgreSQL; não houve execução em PostgreSQL real ou inspeção visual
em navegador. Não usa create_app(), .env ou banco configurado.

Patch preparado somente em /tmp/one-18.2e-1 e validado por dry-run. As bases foram
conferidas por SHA-256 para confirmar que os arquivos do projeto ficaram intactos.
Nenhuma permissão foi alterada. O patch corresponde especificamente a este checkout:
se outro ambiente já tiver a integração da 18.2E, seu estado deve ser conferido
antes da aplicação, evitando usar este diff sobre uma base diferente.
