# Etapa 19.3-D — Certificados no Portal ONE

Base: `feature/certificados-etapa-193`, commit `25a2f576ecb6d0b77113bae020da6535bc1a1f65`.
Branch de trabalho: `feature/certificados-portal-etapa-193d`.

## Comportamento e segurança

- A conclusão consulta disponibilidade sem gravar. A primeira emissão exige conclusão,
  aprovação persistida e histórico consistente, pela mesma validação da etapa 19.3-B.
- `POST /aprender/t/<token>/certificado` exige CSRF, resolve a participação com
  `obter_participacao`/`participacao_valida`, chama `emitir_certificado` e responde 303.
- `GET` na mesma URL somente consulta um certificado já existente e chama
  `gerar_pdf_certificado`. Não chama o emissor nem atualiza histórico.
- IDs enviados em formulário/query não selecionam certificados. O certificado é
  sempre consultado pela participação resolvida pelo mecanismo existente, incluindo
  o filtro de acessos educativos vinculados à sessão.
- Download como `certificado-one.pdf` (nome fixo seguro), `application/pdf`, attachment,
  `Content-Length`, `no-store`, `no-referrer` e `nosniff`, sem ETag.
- A página de resultado oferece acesso à conclusão para participações aprovadas.
- Certificados emitidos mantêm o snapshot histórico, conforme o serviço anterior;
  não há recálculo de notas, reparo de datas ou alteração do layout PDF.
- Acesso inválido/inativo/inexistente: 404; sem aprovação/conclusão: 403;
  histórico inconsistente ou dados PDF incompletos: 409; falha de banco: 503;
  CSRF inválido/ausente: 400. GET elegível sem certificado emitido: 404.
- Não foram adicionados logs de tokens, códigos de verificação ou parâmetros SQL.

## Arquivos

Modificados:
- `app/app/portal_treinamento/routes.py`
- `app/app/services/certificados.py` (consulta sem escrita, compartilhando validação)
- `app/app/templates/portal_treinamento/conclusao.html`
- `app/app/templates/portal_treinamento/resultado_quiz.html`

Criados:
- `app/tests/test_etapa193d_portal.py`
- `docs/etapa193d_portal_certificados.md`

`app/requirements.txt` já declara `reportlab==5.0.1` (arquivo UTF-16); não precisou
ser alterado. O gerador PDF e a migração anterior permanecem intactos.

## Verificação automatizada

Virtualenv separado, dependências instaladas pelo requirements do projeto, Flask
com CSRF ativo e SQLite exclusivamente em memória. Não carrega configuração real
nem usa credenciais ONE. A fixture descarta e recria apenas esse banco em memória.

A partir de `app/`:

```bash
PYTHONPATH=.:tests python -m unittest \
  test_etapa193d_portal \
  test_etapa193c_pdf \
  test_portal_treinamento_quiz.QuizTest \
  test_portal_treinamento_progresso.ProgressoTest -v
```

Resultado: 62 testes aprovados (14 da integração, 7 PDF, 24 quiz e 17 progressão).
Cobrem emissão/download reais, aprovação/reprovação, não conclusão, CSRF ausente,
inválido e de outra sessão, tokens inválidos, outra participação, acesso inativo,
participação removida, repetição idempotente, cabeçalhos, bytes PDF, GET sem emissão,
preservação integral de participações/resultados/submissões e snapshot histórico.

A suíte `test_etapa193b` foi invocada e pulada por ausência de PostgreSQL local
configurado. Não há comprovação nova de concorrência/locks PostgreSQL nesta entrega.

## Revisão e aplicação posterior na VM DEV

1. Buscar a branch e revisar o diff contra `25a2f57`, usando checkout separado e
   sem alterar a `main`. Conferir os mecanismos de acesso e os testes acima.
2. Criar virtualenv de testes e instalar `app/requirements.txt`. Executar a suíte
   acima sem carregar `.env` da aplicação e sem credenciais reais.
3. Reexecutar emissão/concorrência em PostgreSQL **local descartável**, usando um
   banco exclusivo de teste. Definir `ONE_APROVACAO_MIGRATION_TEST_DATABASE_URL` e
   `ONE_PROGRESSO_TEST_DATABASE_URL` para esse mesmo banco local: a suíte 19.3-B
   valida a primeira variável e a fixture compartilhada usa a segunda. Ela cria
   e remove schemas de teste. Nunca apontar para banco real ou produção.

   ```bash
   PYTHONPATH=.:tests python -m unittest test_etapa193b -v
   ```

4. Em homologação DEV separada, confirmar que a migração anterior `a193b001c001`
   já está aplicada. Esta etapa não cria migração. Qualquer migração pendente deve
   ser analisada e aplicada em procedimento separado, com autorização adequada.
5. Validar manualmente aprovação com quiz e sem quiz, reprovação, repetição,
   PDF baixado, acesso educativo por sessão e acesso revogado/expirado. Conferir
   também navegação resultado → conclusão → certificado e o PDF em um leitor.
6. Revisar logs do proxy/servidor: as URLs de token já pertencem ao mecanismo
   existente e precisam continuar protegidas pela configuração operacional.
7. Somente após revisão e homologação decidir aplicação na VM DEV. Não houve
   acesso à produção `172.16.20.189`, deploy, merge na main ou migração nesta tarefa.

Testes automatizados aprovados **não constituem homologação**. Permanecem pendentes
os testes PostgreSQL e a revisão funcional/visual e operacional na VM DEV.
