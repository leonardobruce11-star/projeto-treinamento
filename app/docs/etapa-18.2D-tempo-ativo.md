# Etapa 18.2D — exibição do tempo ativo

## Telas

- Histórico do colaborador: tempo total por participação, incluindo treinamentos
  iniciados e concluídos; sem registros, mostra 0 min.
- Detalhe da campanha: acrescenta Tempo ativo às duas tabelas existentes de
  participantes, mantendo status, progresso, datas, quiz e métricas atuais.
- Relatório HTML/PDF: tempo total na tabela de participantes. O PDF ajusta as
  larguras para caber no A4 e permite quebra de linha em nomes/departamentos/status.
- Página do módulo: total da participação e tempo do módulo atual no cabeçalho.
  É uma fotografia dos valores ao carregar a página, sem atualização por JS.

## Consultas e formatação

app/utils/tempo.py fornece formatar_tempo_ativo, registrado como filtro Jinja
"tempo_ativo" na fábrica da aplicação e utilizado diretamente no PDF.
Zero/None -> 0 min; 45 -> 45 s; 750 -> 12 min 30 s; 4080 -> 1 h 08 min.
Acima de uma hora, omite segundos sem arredondar ou alterar os dados oficiais.

app/utils/tempos_treinamento.py consulta SUM(segundos_ativos) GROUP BY
participacao_id, restringindo aos IDs exibidos. Uma consulta cobre uma listagem;
IDs sem registros recebem zero. Lista vazia não executa consulta.
No portal, duas consultas fixas obtêm SUM e tempos dos módulos da participação.
Não acessa a coleção lazy tempos_treinamento nem consulta tempos por linha.
Relacionamentos utilizados nas listagens são carregados com selectinload;
SQLAlchemy pode dividir listas grandes em lotes, sem uma consulta por participante.
A consulta explícita de quiz por participante no cálculo de métricas foi trocada
pelo relacionamento carregado em lote, mantendo os cálculos e a assinatura.

## Correção necessária no detalhe da campanha

O return de render_template estava dentro do else de total_quizzes_campanha.
Foi alinhado ao corpo da função para retornar também quando existem quizzes.
Os testes cobrem ambos os ramos e verificam os indicadores preservados.

## Validação

    python -B -m unittest discover -s tests -p test_tempo_ativo_exibicao.py -v
    node --test tests/test_portal_treinamento_heartbeat_browser.cjs

11 testes Python passaram com SQLite em memória, sem create_app(), leitura de
.env ou acesso ao banco configurado. Incluem SQL real, contagem de consultas,
rotas, renderização Jinja e geração real do PDF pelo ReportLab.
Os 9 testes existentes de navegador passaram.
Não houve execução em PostgreSQL, inspeção visual do PDF ou navegador real.
A suíte HTML usa contexto simplificado e Undefined permissivo para os dados
alheios ao tempo ativo; não substitui teste completo da aplicação autenticada.

O patch não altera modelos, migrations, dashboard, feedback, coleta ou JS.
Heartbeat, CSRF e funções de início/avanço/quiz/conclusão do portal foram
comparados por AST com os originais e permaneceram idênticos.
Nenhuma alteração foi aplicada ao projeto: proposta e patch ficam em /tmp.
