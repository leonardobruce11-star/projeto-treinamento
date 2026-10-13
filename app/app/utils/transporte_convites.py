"""Contrato de transporte da 18.6B. Sem composição de e-mail ou SMTP."""
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class DadosEnvio:
    envio_id: int
    participacao_id: int
    destinatario: str = field(repr=False)
    message_id: UUID


@dataclass(frozen=True)
class ResultadoTransporte:
    estado: str
    codigo_erro: str | None = None


class TransporteConvites(Protocol):
    def enviar(self, dados: DadosEnvio) -> ResultadoTransporte: ...


class TransporteFalso:
    """Backend de testes/DEV: guarda só a quantidade de chamadas, sem payload."""
    def __init__(self, resultado='enviado'):
        if resultado not in ('enviado', 'falha', 'resultado_indeterminado'):
            raise ValueError('Resultado falso inválido.')
        self.resultado = resultado
        self.chamadas = 0

    def enviar(self, dados: DadosEnvio) -> ResultadoTransporte:
        self.chamadas += 1
        erro = {'falha': 'transporte_falso_falha',
                'resultado_indeterminado': 'transporte_falso_indeterminado'}.get(self.resultado)
        return ResultadoTransporte(self.resultado, erro)
