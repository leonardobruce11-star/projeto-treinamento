# Etapa 18.2C — atividade no navegador

Somente o template de módulo carrega o controlador, após a validação da
participação e do acesso na rota existente. Nenhuma rota é alterada.

A página visível começa com uma janela de atividade de 60 segundos.
Mouse (movimento ou botão), teclado, toque e scroll renovam essa janela.
Ao atingir 60 segundos sem interação, nenhum heartbeat é enviado.
Eventos não têm preventDefault e não interferem na navegação/formulários.

Cada heartbeat envia POST com {"segundos": 15}, Content-Type application/json,
X-CSRFToken gerado por csrf_token() e cookies da mesma origem. O primeiro envio
ocorre após 15 segundos. Os seguintes são agendados 15 segundos após a resposta;
requisições não se sobrepõem. Falhas HTTP e de rede são silenciosas.

Ocultar a aba cancela o timer. Retornar à aba ou interagir após inatividade
inicia outro intervalo completo, sem envio imediato nem compensação da pausa.
Uma requisição já enviada pode terminar depois que a aba é ocultada.
O payload é sempre 15; performance.now() serve apenas para detectar inatividade.
O total oficial e o incremento aceito continuam sob autoridade do servidor.
Uma resposta pendente impede novos envios, sem impedir o uso do treinamento.
Reinicializar destrói o controlador anterior, seus listeners e seu timer.
pagehide suspende o timer e pageshow retoma após 15 segundos, inclusive no BFCache.

Limitação: visibilidade e eventos são aproximações simples de interação;
leitura sem eventos por 60 segundos pausa o envio. Não há detecção de vídeo,
controle entre abas, mudança de progresso ou implementação de etapas posteriores.

Validação sem dependências adicionais:

    node --test tests/test_portal_treinamento_heartbeat_browser.cjs

A suíte executa o script real em um DOM simulado, com timers e rede controlados.
Valida visibilidade, inatividade, retomada, payload/CSRF, falhas, reinicialização,
requisições pendentes e ausência de interceptação do formulário. Não substitui
um teste manual em navegador nem a suíte PostgreSQL da etapa 18.2B.
