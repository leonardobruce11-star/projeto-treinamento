# Etapa 19.4-D — Download administrativo de certificados PDF

Base: `feature/certificados-admin-etapa-194c`, commit `66da533`.
Branch: `feature/certificados-admin-etapa-194d`.

## Arquitetura e fluxo

Foi adicionada `GET /certificados/<int:certificado_id>/pdf` no blueprint
`certificados_admin`, com HEAD automático e autorização administrativa antes da
consulta ou renderização. A rota reutiliza:

1. `obter_certificado_emitido(certificado_id)`, que consulta diretamente o registro
   persistido em sessão independente, sem autoflush ou commit, e retorna o recibo
   histórico imutável.
2. `gerar_pdf_certificado(recibo)`, da etapa 19.3-C, cuja API recebe o recibo e
   retorna bytes. O código do gerador e seu layout não foram modificados.
3. `send_file(BytesIO(conteudo), ...)`, para entregar o documento em memória,
   sem arquivo temporário ou permanente no servidor.

Nome, título, código e emissão são os snapshots persistidos do certificado, sem
substituição por dados atuais. Não são carregados os cadastros vinculados nem a
participação. Cadastros inativos e divergências posteriores de aprovação não
bloqueiam o documento histórico existente. Ausência de certificado nunca provoca
emissão, mesmo para participação aprovada.

Uma checagem defensiva exige bytes, assinatura `%PDF-` e marcador final `%%EOF`
antes de construir a resposta de download. É uma checagem básica do retorno, sem
introduzir parser PDF ou duplicar o gerador. Não é uma validação completa da
estrutura do documento.

HEAD percorre a mesma geração em memória para produzir os cabeçalhos reais,
inclusive Content-Length; Flask/Werkzeug suprime o corpo. Não há escrita em banco,
emissão ou persistência de PDF em GET/HEAD.

## Autorização e rota

A rota usa o decorador existente `administrador`, de `convites/routes.py`, sem
regras duplicadas:

- Administrador ativo: 200 e PDF para documento existente.
- Anônimo: redirecionamento ao login.
- Operador: 403.
- Administrador inativo: redirecionamento ao login pelo user_loader real.
- Certificado inexistente ou ID fora do domínio válido: 404.

IDs de formato incompatível com o conversor inteiro da rota também retornam 404.
Parâmetros de query string não selecionam certificado, não escolhem o nome do
arquivo e não substituem campos históricos. Tokens do portal não são utilizados.
Não há emissão manual ou validação pública por código.

## Cabeçalhos HTTP

Sucesso:

- `Content-Type: application/pdf`
- `Content-Disposition: attachment; filename=certificado-<id>.pdf`
- `Content-Length`: tamanho do PDF gerado.
- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`

O nome usa apenas um prefixo fixo e o ID inteiro validado; não inclui nomes,
títulos, códigos, tokens ou outras informações pessoais. ETag e processamento
condicional foram desabilitados. Não há arquivo em disco para Last-Modified.
Os cabeçalhos de privacidade do blueprint também são preservados nas respostas de
erro e nos bloqueios de autorização da rota reconhecida.

## Tratamento de falhas

| Situação | Resposta |
| --- | --- |
| Ausência/ID inválido | 404, `certificado_indisponivel`. |
| Erro SQLAlchemy na consulta | 503, `consulta_indisponivel`. |
| ValueError do gerador, inclusive dados obrigatórios ausentes | 409, `dados_certificado_invalidos`. |
| Outra exceção do gerador | 503, `geracao_indisponivel`. |
| Retorno vazio, tipo inválido, assinatura ausente ou sem marcador EOF | 503, `geracao_indisponivel`. |

Erros controlados retornam JSON genérico, sem Content-Disposition de download ou
MIME de PDF. A resposta PDF só é criada após a geração terminar e a checagem básica
passar. Não são enviados fragmentos do documento em caso de falha. A captura de
exceções gerais está limitada à chamada ao renderizador; não registra ou devolve
SQL, caminhos internos, nomes, códigos ou stack traces.

## Integração visual e contratos preservados

O template de detalhes ganhou o botão **Baixar PDF**, com os estilos existentes.
O botão aparece apenas quando um certificado foi consultado com sucesso. O link
aponta para a rota administrativa por ID, sem filtros ou tokens no URL do PDF.

O botão **Voltar aos certificados** e seu contexto de filtros/página não foram
alterados. A listagem continua oferecendo Visualizar detalhes; não foi adicionada
uma segunda ação de download na tabela, mantendo sua organização.

Permanecem intactos o contrato JSON da etapa A, filtros/paginação da etapa B,
dados HTML da etapa C, serviço de consulta, emissão e PDF do portal. Não foram
criadas operações de edição, revogação, exclusão ou funcionalidades da etapa 20.

## Arquivos criados

- `app/tests/test_etapa194d_download_admin_certificados.py`
- `docs/etapa194d_download_admin_certificados.md`

## Arquivos modificados

- `app/app/certificados_admin/routes.py`: integração do download, erros e headers.
- `app/app/templates/certificados_admin/detalhes.html`: botão Baixar PDF.
- `app/tests/test_etapa194a_consulta_certificados.py`: a asserção de rotas passa
  a admitir PDF, mantendo bloqueadas edição e revogação.
- `app/tests/test_etapa194c_detalhes_certificados.py`: admite o novo botão/rota e
  mantém as verificações contra funcionalidades de mutação.

Não foram alterados modelos, migrações, dependências, serviço de emissão, gerador
PDF ou rotas do portal. `reportlab` já está declarado no requirements existente.

## Testes executados

Virtualenv de testes separado, com as dependências declaradas pelo projeto. Os
novos testes reutilizam a factory real e fixtures com SQLite exclusivamente em
memória, Flask-Login habilitado, ambiente fictício e `load_dotenv` substituído por
mock. Não carregam credenciais ou dados corporativos.

A partir de `app/`, com o virtualenv de testes ativado:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python -m unittest \
  test_etapa194d_download_admin_certificados \
  test_etapa194c_detalhes_certificados \
  test_etapa194b_listagem_certificados \
  test_etapa194a_consulta_certificados \
  test_etapa193d_portal \
  test_etapa193c_pdf \
  test_portal_treinamento_quiz.QuizTest \
  test_portal_treinamento_progresso.ProgressoTest \
  test_admin_convites.AdminConvitesTest \
  test_convites_visao_geral.VisaoGeralTest \
  test_painel_colaboradores_pagina.PainelPaginaTest \
  test_painel_colaboradores_filtros.FiltrosTest \
  test_metricas_feedback.MetricasFeedbackTest -q
```

Resultado: **212 aprovados, 0 falhos, 0 pulados**, incluindo 25 testes novos da
etapa D. Os testes anteriores de A/B/C, PDF e portal passaram.

Cobertura nova: administrador ativo, anônimo, operador, administrador inativo e
desativado após acesso; inexistência e IDs inválidos; headers, bytes PDF reais,
assinatura/EOF, Content-Length, filename seguro; HEAD sem corpo; buffer em memória;
snapshots enviados ao gerador real após alterações cadastrais; cadastros inativos;
aprovação sem documento; ausência de revalidação; repetição GET/HEAD sem escrita;
consulta única sem relacionamentos; falha SQL; ValueError/RuntimeError/OSError do
gerador; retorno vazio/inválido/truncado; botão e retorno com filtros/página.

Snapshots de todas as tabelas e captura de SQL verificam ausência de escrita.
Mocks detectam chamadas ao emissor e à validação de elegibilidade. Bloqueios de
permissão são testados antes da consulta e do gerador. O envio em memória é
verificado envolvendo o send_file real, sem substituir a geração normal por PDF
fictício nos testes de sucesso.

## Limitações e pendências

- PostgreSQL descartável não estava configurado nesta execução; suas variantes
  não foram selecionadas no comando. Revalidar em PostgreSQL isolado na revisão
  posterior autorizada.
- HEAD realiza o trabalho de geração para apresentar cabeçalhos corretos; esse
  custo é deliberado e não implica escrita em banco ou armazenamento de arquivo.
- Regenerações podem variar em bytes/metadados próprios do ReportLab; os campos
  históricos permanecem os mesmos. Não foi introduzido armazenamento de binários.
- Revisão visual do botão e do PDF baixado e homologação funcional ficam pendentes.
  Testes aprovados não constituem homologação.
- Não houve desvio de escopo, nova dependência ou necessidade de migração.

A tarefa foi executada somente no ambiente isolado do Codex Cloud. Não houve
acesso à VM DEV `172.16.20.79`, à produção `172.16.20.189` ou a bancos corporativos.
Não houve migração, reinício de serviços, merge na main ou deploy.
