"""Consultas de documentos já emitidos, sem emissão ou autorização HTTP."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.models.certificado_treinamento import CertificadoTreinamento
from app.services.certificados import ErroCertificado, ReciboCertificado, recibo_de_certificado


CERTIFICADOS_POR_PAGINA = 20


@dataclass(frozen=True)
class FiltrosCertificados:
    participante: str
    treinamento: str
    codigo: str


@dataclass(frozen=True)
class PaginaCertificados:
    itens: tuple[ReciboCertificado, ...]
    filtros: FiltrosCertificados
    pagina: int
    paginas: int
    total: int


def _filtro_textual(valor, limite):
    if (
        not isinstance(valor, str)
        or len(valor) > limite
        or any(ord(letra) < 32 or ord(letra) == 127 for letra in valor)
    ):
        raise ValueError("filtro_invalido")
    return valor.strip()


def listar_certificados_emitidos(*, participante="", treinamento="", codigo="", pagina=1):
    """Retorna uma página de snapshots emitidos, sem consultar cadastros atuais.

    Os três filtros são trechos literais, sem distinção de maiúsculas, unidos
    por AND. Página fora do conjunto é limitada à última (ou 1 no vazio).
    O chamador deve autorizar a consulta antes de expor qualquer dado.
    """
    if type(pagina) is not int or not 1 <= pagina <= 2147483647:
        raise ValueError("pagina_invalida")
    filtros = FiltrosCertificados(
        participante=_filtro_textual(participante, 150),
        treinamento=_filtro_textual(treinamento, 200),
        codigo=_filtro_textual(codigo, 64),
    )
    condicoes = []
    for coluna, valor in (
        (CertificadoTreinamento.nome_participante, filtros.participante),
        (CertificadoTreinamento.titulo_treinamento, filtros.treinamento),
        (CertificadoTreinamento.codigo_verificacao, filtros.codigo),
    ):
        if valor:
            literal = valor.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            condicoes.append(coluna.ilike("%" + literal + "%", escape="!"))
    with Session(db.engine, autoflush=False) as session:
        total = session.execute(
            select(func.count(CertificadoTreinamento.id)).where(*condicoes)
        ).scalar_one()
        paginas = max(1, (total + CERTIFICADOS_POR_PAGINA - 1) // CERTIFICADOS_POR_PAGINA)
        pagina = min(pagina, paginas)
        certificados = session.execute(
            select(CertificadoTreinamento).where(*condicoes)
            .order_by(CertificadoTreinamento.emitido_em.desc(), CertificadoTreinamento.id.desc())
            .limit(CERTIFICADOS_POR_PAGINA)
            .offset((pagina - 1) * CERTIFICADOS_POR_PAGINA)
        ).scalars().all()
        return PaginaCertificados(
            itens=tuple(recibo_de_certificado(c) for c in certificados),
            filtros=filtros, pagina=pagina, paginas=paginas, total=total,
        )


def obter_certificado_emitido(certificado_id):
    """Retorna ReciboCertificado ou None quando o documento não existe.

    Exige ID inteiro positivo do domínio Integer do modelo. O chamador deve
    autorizar o acesso administrativo. A sessão independente evita autoflush
    de alterações pendentes do chamador; não há commit nem consulta de
    elegibilidade, cadastros atuais ou tokens de participação.
    """
    if type(certificado_id) is not int or not 1 <= certificado_id <= 2147483647:
        raise ErroCertificado("certificado_invalido")
    with Session(db.engine, autoflush=False) as session:
        certificado = session.execute(
            select(CertificadoTreinamento).where(
                CertificadoTreinamento.id == certificado_id
            )
        ).scalar_one_or_none()
        return recibo_de_certificado(certificado) if certificado is not None else None
