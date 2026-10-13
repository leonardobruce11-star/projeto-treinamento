# Etapa 18.3B — validação central de módulos

Proposta preparada em 2026-10-01 a partir de /opt/One_treino/app. Não aplicada.

## Regra

`calcular_progresso` ordena módulos por `(ordem, id)`. `ultimo_modulo_id` representa o último módulo do prefixo concluído. Cursor nulo libera apenas o primeiro. Módulos desse prefixo permitem revisão; apenas o próximo pendente aceita avanço. Módulos posteriores permanecem bloqueados. O utilitário expõe concluídos, liberados, bloqueados, próximo pendente e término da sequência.

GET e POST usam o mesmo estado. GET futuro redireciona para o pendente, sem liberar o solicitado. POST futuro retorna 403. Token inválido, treinamento inativo ou módulo alheio retornam 404. Cursor apontando fora da sequência retorna 409 e não é reparado automaticamente. Sequência vazia não implica conclusão e não permite avanço.

POST bloqueia a participação com SELECT FOR UPDATE e recarrega seu estado com populate_existing. Valida e atualiza o cursor na mesma transação. Um POST do pendente avança exatamente uma posição e registra iniciado_em apenas se ainda nulo. Repetições e POSTs atrasados de módulos já concluídos não gravam nada: liberam a transação e redirecionam ao pendente atual, ou ao quiz quando não resta módulo. Assim não regridem o cursor nem alteram datas. Tentativas inválidas terminam com rollback. Erro SQL retorna 500 após rollback.

A ordenação canônica também é usada nas listas de início e quiz, para preservar a mesma definição de primeiro/último módulo quando há ordens duplicadas. As validações, respostas, pontuação e conclusão do quiz não são reforçadas nesta subetapa.

## Escopo e limites

Arquivos: app/portal_treinamento/routes.py, novo app/utils/progresso_treinamento.py, novo tests/test_portal_treinamento_progresso.py e este documento.

Sem migrations, alterações de modelos ou templates. Heartbeat, tempo ativo, métricas e rota de conclusão permanecem intactos. O avanço não grava resultados de quiz nem conclusão. Bloqueio de reordenação, respostas obrigatórias, nota mínima e validação final pertencem às próximas subetapas. O cursor existente é interpretado como prefixo; esta mudança não comprova nem corrige retroativamente o percurso histórico.

## Validação

99 testes passaram, sem falhas ou skips na execução final:

- 37 novos testes de progressão: 17 em SQLite e 20 em PostgreSQL 16 real.
- 62 testes existentes da 18.2: 53 Python e 9 Node.

Cobertura: primeiro módulo, GET/POST futuros, primeiro e segundo avanços, repetição, POST atrasado, último módulo direto, token inválido, módulo alheio, sequência de um módulo e vazia, cursor inconsistente, ordens duplicadas, revisão, CSRF e término da sequência sem concluir o treinamento.

Concorrência PostgreSQL: seis POSTs simultâneos do primeiro módulo avançam uma única posição; seis POSTs simultâneos do último são rejeitados; requisição com cursor antigo espera o bloqueio e valida o cursor atualizado, sem regredir.

PostgreSQL temporário em /tmp, schemas exclusivos de testes e encerramento após execução. Nenhuma instalação de dependências, escrita no banco da aplicação ou modificação do checkout.

Execução Python: python -B -m unittest discover -s tests -p 'test_*.py' -v. Configurar ONE_PROGRESSO_TEST_DATABASE_URL e ONE_HEARTBEAT_TEST_DATABASE_URL para PostgreSQL exclusivo de testes. A suíte de progressão PostgreSQL é opcional sem sua variável; a suíte de heartbeat exige a variável correspondente. Nunca apontar para banco de produção.

Execução navegador: node --test tests/test_portal_treinamento_heartbeat_browser.cjs.
