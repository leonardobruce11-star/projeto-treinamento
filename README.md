# Plataforma de Treinamento em Segurança — ONE

Plataforma corporativa de treinamento e conscientização em segurança da informação para a ONE Consultoria Tributária, produto principal do TCC de Redes de Computadores da FATEC Osasco.

## Estado do repositório

Estrutura inicial para organizar a importação da aplicação existente na VM e os artefatos acadêmicos. Este repositório ainda não contém uma aplicação executável. Não há comandos de instalação ou execução validados para esta estrutura.

## Tecnologias e convenções verificadas no contexto

- Python e Flask, com fábrica de aplicação `create_app()`.
- PostgreSQL.
- Apache e Gunicorn no ambiente da plataforma.
- Organização por módulos, com arquivos `routes.py` e `service.py`; módulos já mencionados: `dashboard` e `portal_treinamento`.
- Templates Jinja2/HTML e recursos CSS/JavaScript.
- Gophish como integração externa de simulações; a plataforma é a fonte de verdade.
- Histórico de Docker/WSL2 no projeto. Configuração atual de implantação e versões das dependências devem ser conferidas na VM antes da importação.

O projeto de análise de risco com Ollama e os exercícios acadêmicos em PHP são trabalhos separados.

## Organização

| Caminho | Conteúdo |
| --- | --- |
| `app/` | Aplicação Flask a importar da VM |
| `app/dashboard/` | Módulo do painel |
| `app/portal_treinamento/` | Módulo do portal de treinamento |
| `app/templates/` | Templates Jinja2/HTML |
| `app/static/css/`, `js/`, `img/` | Recursos da interface |
| `docs/tcc/` | Texto e documentação acadêmica |
| `docs/requisitos/` | Requisitos e rastreabilidade |
| `docs/psi/` | PSI complementar e requisitos derivados |
| `modelagem/contexto/` | Diagrama de contexto |
| `modelagem/objetos/` | Diagramas de objetos |
| `modelagem/dfd/` | Diagramas de fluxo de dados |
| `modelagem/banco/` | Modelo conceitual/lógico e dicionário de dados |
| `wireframes/` | Telas e fluxos |
| `banco/sql/` | Scripts SQL revisados |
| `banco/seeds/` | Dados exclusivamente fictícios |
| `migrations/` | Migrações reais a importar preservando o histórico |
| `deploy/` | Configuração de implantação a conferir na VM |

As pastas são uma base organizacional, não uma exigência de reorganização do código existente. Antes de mover arquivos, conferir imports, blueprints, caminhos de templates e implantação.

## Escopo acadêmico

O foco do TCC é a engenharia e modelagem da plataforma corporativa. A PSI é complementar e fornece requisitos para os treinamentos. O trabalho anterior de SGSI/análise de risco permanece separado.

## Convenções de contribuição

- Nomes de pastas em minúsculas, sem espaços ou acentos; seguir os nomes existentes dos módulos.
- Documentação em português, com links relativos e situação dos artefatos explícita.
- Preservar a estrutura dos módulos e a fábrica Flask ao importar a aplicação.
- Registrar dependências e versões a partir do ambiente validado, sem inventar versões.
- Não versionar chaves de API, senhas, tokens, arquivos `.env`, dados reais, dumps de produção ou registros de campanhas.
- Preservar segurança e idempotência das operações de integração.
- Usar dados simulados nos exemplos e na documentação.

## Próximas etapas

1. Inventariar e importar o código da VM, conferindo credenciais e dados antes do commit.
2. Importar dependências, configurações sem segredos e o histórico real de migrações.
3. Documentar a execução local e a implantação a partir de um teste reproduzível.
4. Elaborar requisitos, modelos, wireframes e documentação do TCC alinhados à aplicação.
