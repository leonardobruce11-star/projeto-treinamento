"""Consultas de documentos já emitidos, sem emissão ou autorização HTTP."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extensions import db
from app.models.certificado_treinamento import CertificadoTreinamento
from app.services.certificados import ErroCertificado, recibo_de_certificado


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
