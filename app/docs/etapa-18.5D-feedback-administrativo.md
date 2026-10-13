# Etapa 18.5D — feedback administrativo

Página autenticada: `GET /treinamentos/feedback`. Filtros: treinamento e categoria.
O detalhamento usa `treinamento_id`; a paginação de comentários usa `page`.
A comparação é ordenada por título e ID, sem ranking.

## Consultas e taxa de resposta

`app/utils/metricas_feedback.py` agrega conclusões e feedbacks em duas
subconsultas SQL independentes, agrupadas por treinamento. O LEFT JOIN dessas
subconsultas com treinamentos inclui treinamentos sem avaliações. Não há joins
com colaboradores, módulos, tempos ou quizzes nas consultas gerenciais.

As quatro médias usam AVG; as distribuições usam SUM(CASE WHEN nota = N THEN 1
ELSE 0 END), para N de 1 a 5. A contagem usa COUNT. As respostas consideradas
são feedbacks vinculados a participações com `concluido_em` preenchido, conforme
a regra de envio das etapas 18.5B/C. Nenhum legado é alterado pela leitura.

Taxa = 100 × feedbacks de participações concluídas / participações concluídas.
Uma participação concluída sem feedback entra somente no denominador.
Participações não concluídas ficam fora dos dois termos. Repetições contam como
participações distintas. O denominador zero retorna NULL via NULLIF, exibido
como “—”; com conclusões e nenhuma resposta, a taxa é 0%. O resumo geral usa
a razão dos totais, e não a média das taxas de treinamento.

## Privacidade

O vínculo técnico no banco permanece. A saída gerencial não consulta nem
apresenta nome, e-mail, departamento, token ou identificação da participação.
Não há promessa de anonimato. O texto livre pode conter identificação escrita
pelo próprio autor; o sistema o apresenta como texto simples e escapado, sem
associação a dados cadastrais.

O limiar de cinco avaliações é aplicado por treinamento antes da apresentação,
inclusive na listagem geral e em filtros de categoria. A contagem e a taxa
continuam disponíveis. Médias e distribuições retornam NULL na própria consulta
SQL quando houver menos de cinco respostas. Médias também ficam ocultas porque
uma única resposta é revelada integralmente pela média e grupos pequenos
favorecem inferências. Não existe média global que permita reconstruir uma
média suprimida por subtração.

Comentários são elegíveis somente se o treinamento tiver pelo menos cinco
avaliações; juntar treinamentos não contorna esse limiar. A proteção conta
avaliações, não comentários e não o tamanho da página. São dez comentários por
página, ordenados por data e ID decrescentes. A consulta ignora NULL e texto
composto exclusivamente de espaços em branco. A saída contém somente texto,
data e título do treinamento. O escape padrão do Jinja permanece habilitado.

No perfil do colaborador, uma consulta em lote seleciona exclusivamente
participação e data do feedback. A interface exibe “Avaliação enviada” e sua
data ou “Sem avaliação”. Não carrega notas nem comentários individuais.

## Escopo e verificação

Sem migration, escrita no banco, exportação ou inclusão em relatórios HTML/PDF.
A etapa 18.5E não está incluída. O acesso segue `login_required`, utilizado
pelas rotas administrativas existentes; não é criado um novo sistema de perfis.

Os testes de `test_metricas_feedback.py` exercitam as consultas e os templates
reais em SQLite e PostgreSQL isolados. A página geral executa seis consultas,
independentemente do número de respostas; o filtro de treinamento acrescenta
uma consulta de existência. O histórico adiciona uma consulta em lote.

Para executar todas as regressões, configurar `ONE_PROGRESSO_TEST_DATABASE_URL`
e `ONE_HEARTBEAT_TEST_DATABASE_URL` com um PostgreSQL exclusivo de testes e usar
`python -B -m unittest discover -s tests -v`. O teste JavaScript roda com
`node --test tests/test_portal_treinamento_heartbeat_browser.cjs`.
