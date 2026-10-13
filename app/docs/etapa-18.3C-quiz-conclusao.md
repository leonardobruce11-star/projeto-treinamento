# Etapa 18.3C — validação de quiz e conclusão

Proposta preparada em 2026-10-01 sobre o checkout real /opt/One_treino/app, com a 18.3B já aplicada. Não aplicada ao projeto.

## Arquivos

- app/portal_treinamento/routes.py: GET/POST do quiz, conclusão e validador de respostas.
- app/templates/portal_treinamento/quiz.html: mensagem acessível de validação, reutilizando estilo existente.
- tests/test_portal_treinamento_quiz.py: novos testes isolados, reutilizando fixtures da 18.3B.
- Este documento.

## Elegibilidade e respostas

Participações ainda não concluídas usam calcular_progresso da 18.3B no GET e POST. Apenas todos_modulos_concluidos libera avaliação/finalização. GET antecipado redireciona ao próximo módulo pendente, ou ao início se não há módulos. POST antecipado retorna 403. Cursor inconsistente retorna 409, sem reparo automático. Sequência vazia não permite conclusão.

O modelo atual não possui flag de questão opcional: todas as questões cadastradas são exigidas, em consonância com o formulário existente. O servidor exige exatamente uma alternativa por questão e confirma que a alternativa pertence àquela questão. Ausência, resposta parcial, valor inexistente, alternativa de outra questão/treinamento e campo de resposta duplicado são rejeitados com 400. Campos questao_* desconhecidos também são rejeitados; outros campos, como CSRF, não participam da pontuação. Nenhum ResultadoQuiz é criado antes da validação completa. A mensagem de validação aparece no formulário.

Questões e alternativas são carregadas com selectinload, evitando uma consulta de alternativas por questão. A pontuação permanece total_acertos / total_questoes * 100, arredondada para duas casas. Não há nota mínima.

## Idempotência e transação

POST bloqueia a participação com SELECT FOR UPDATE, usando populate_existing para ler o cursor atual após a espera. O mesmo bloqueio empregado pela 18.3B serializa avanço e conclusão. Depois da trava, o servidor consulta o resultado existente e verifica concluido_em. Resultado e conclusão são gravados na mesma transação. A unicidade existente de resultados_quiz.participacao_id permanece como proteção adicional; nenhuma migration é necessária.

Reenvio de participação já concluída apresenta o resultado existente ou redireciona à conclusão, sem criar resultado nem alterar datas. O treinamento sem quiz exige POST explícito e módulos concluídos; GET elegível apenas apresenta a opção de finalizar. Reenvios sem quiz preservam a data registrada. Finalmente, rollback libera a trava de tentativas inválidas, leituras e reenvios; falha SQL desfaz a transação e retorna 500.

Um resultado legado sem concluido_em, apesar de progressão completa, retorna 409 no quiz e não é corrigido automaticamente.

## Conclusão e histórico

A rota de conclusão exige concluido_em. Sem essa data, redireciona ao módulo pendente ou ao quiz; cursor inconsistente retorna 409. Participações já concluídas continuam exibindo seu histórico mesmo se os módulos atuais divergem do cursor. Essa divergência é registrada como aviso no servidor e não modifica dados.

Consulta/reenvio de resultado histórico é uma exceção à elegibilidade da avaliação nova: não apresenta formulário nem processa respostas. Se uma participação concluída não tem resultado, um quiz cadastrado posteriormente não gera nova avaliação ou resultado automaticamente.

A progressão usa o cursor da 18.3B como representação do prefixo concluído. Não existe registro individual de avanço para provar retroativamente sequências anteriores ao controle rígido. Preservar esse histórico não libera novas conclusões antecipadas.

## Escopo preservado

Sem alterações em heartbeat, tempo ativo, pontuação, módulos, regras de reordenação, modelos ou migrations. Sem nota mínima e sem implementação da 18.3D. GETs não gravam progresso, resultados ou conclusão.

## Testes e execução

Novos testes cobrem GET/POST antecipados, progresso parcial, formulário elegível sem escrita, respostas vazias/parciais/completas/inválidas/estrangeiras/duplicadas, reenvios, treinamento sem quiz, conclusão sem/com data válida, histórico divergente, inconsistência legada, sequência vazia, CSRF, token inválido, falha de commit e compilação dos templates.

PostgreSQL real testa seis envios simultâneos válidos com criação de exatamente um resultado; seis finalizações simultâneas sem quiz; e seis envios antecipados rejeitados. Reexecutar também integralmente a 18.2 e a 18.3B.

Comandos: python -B -m unittest discover -s tests -p 'test_*.py' -v; node --test tests/test_portal_treinamento_heartbeat_browser.cjs.

As variáveis ONE_PROGRESSO_TEST_DATABASE_URL e ONE_HEARTBEAT_TEST_DATABASE_URL devem apontar a um PostgreSQL exclusivo de testes. As fixtures criam schemas isolados; sem a primeira variável os testes PostgreSQL de progresso/quiz são ignorados, e a suíte de heartbeat exige sua variável. Nunca apontar para produção.

A validação desta proposta utiliza PostgreSQL 16 temporário em /tmp e encerra o servidor ao final. Sem instalação de dependências ou escrita no banco da aplicação. Os detalhes da execução final ficam nos logs anexos à proposta temporária.

## Resultado final

150 testes passaram, sem falhas ou skips: 51 novos (24 SQLite e 27 PostgreSQL), 37 da 18.3B e 62 da 18.2. A execução Python teve 141 testes em 59,740 s; Node teve 9 testes. Templates do quiz, sem quiz, resultado e conclusão compilaram. Falhas SQL simuladas e avisos históricos nos logs são resultados esperados dos testes correspondentes.

Na primeira execução integrada, a cópia temporária omitia o pacote Python legível app/integracoes/gophish, impedindo carregar um teste de exibição da 18.2. A cópia foi corrigida a partir do checkout; a execução final acima passou integralmente. Não houve correção funcional relacionada a essa falha de infraestrutura.
