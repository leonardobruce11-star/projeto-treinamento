"""Metadata esperada antes da entidade D2, sem relaxar checks de C1/C3."""
from sqlalchemy import MetaData
from app.extensions import db


def metadata_c3():
    metadata = MetaData()
    for table in db.metadata.tables.values():
        if table.name != 'submissoes_quiz':
            table.to_metadata(metadata)
    table = metadata.tables['resultados_quiz']
    table.constraints.remove(next(c for c in table.constraints
        if c.name == 'uq_resultados_quiz_participacao_id'))
    return metadata
