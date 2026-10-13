"""Execução finita, somente fake, sobre a reserva da 18.6B."""
import click
from flask import current_app
from flask.cli import with_appcontext
from app.convites.service import verificar_configuracao
from app.utils.mail_convites import TransporteConfigurado, processar_convite_configurado


def processar_fila(cfg, limite=None, *, resultado_fake='enviado'):
    verificar_configuracao(cfg)
    if limite is not None and (type(limite) is not int or limite <= 0):
        raise ValueError('Limite deve ser positivo.')
    quantidade = min(limite or cfg.batch_size, cfg.batch_size)
    transporte = TransporteConfigurado(cfg)
    transporte.fake.resultado = resultado_fake
    processados = 0
    for _ in range(quantidade):
        if processar_convite_configurado(transporte) is None:
            break
        processados += 1
    return processados


def registrar_cli(app):
    @app.cli.group('convites')
    def convites():
        """Administração local da fila de convites fake."""

    @convites.command('processar')
    @click.option('--limite', type=click.IntRange(min=1), default=None)
    @click.option('--resultado-fake', type=click.Choice(['enviado', 'falha', 'resultado_indeterminado']), default='enviado')
    @with_appcontext
    def processar(limite, resultado_fake):
        """Processa no máximo MAIL_BATCH_SIZE itens, sem SMTP real."""
        try:
            n = processar_fila(current_app.extensions['mail_convites_config'], limite,
                              resultado_fake=resultado_fake)
        except ValueError:
            # Nenhuma exceção bruta de configuração/transporte é exibida.
            raise click.ClickException('Worker bloqueado: verifique backend fake, habilitação e configuração institucional.') from None
        click.echo(f'Backend fake: {n} solicitação(ões) processada(s). Consulte os estados na interface.')
