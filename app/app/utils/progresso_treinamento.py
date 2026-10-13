"""Estado de progressão por cursor; não depende de tempo ativo ou quiz."""
from dataclasses import dataclass


def ordenar_modulos(modulos):
    """Sequência canônica dos módulos persistidos, inclusive ordens repetidas."""
    return tuple(sorted(modulos, key=lambda modulo: (modulo.ordem, modulo.id)))


@dataclass(frozen=True)
class EstadoProgresso:
    modulos: tuple
    indice_ultimo_concluido: int
    cursor_consistente: bool

    @property
    def quantidade_concluidos(self):
        return len(self.modulos_concluidos)

    @property
    def percentual_concluido(self):
        return int(self.quantidade_concluidos / len(self.modulos) * 100) if self.modulos else 0

    def status_modulo(self, modulo_id, modulo_atual_id=None):
        if self.ja_concluido(modulo_id):
            return "Concluído · Em revisão" if modulo_id == modulo_atual_id else "Concluído"
        if self.pode_avancar(modulo_id):
            return "Próximo módulo pendente"
        return "Bloqueado"

    @property
    def modulos_concluidos(self):
        if not self.cursor_consistente:
            return ()
        return self.modulos[:self.indice_ultimo_concluido + 1]

    @property
    def proximo_modulo_pendente(self):
        indice = self.indice_ultimo_concluido + 1
        if self.cursor_consistente and indice < len(self.modulos):
            return self.modulos[indice]
        return None

    @property
    def modulos_liberados(self):
        if not self.cursor_consistente:
            return ()
        return self.modulos[:self.indice_ultimo_concluido + 2]

    @property
    def modulos_bloqueados(self):
        if not self.cursor_consistente:
            return self.modulos
        return self.modulos[self.indice_ultimo_concluido + 2:]

    @property
    def todos_modulos_concluidos(self):
        return (bool(self.modulos) and self.cursor_consistente
                and self.indice_ultimo_concluido == len(self.modulos) - 1)

    def modulo_por_id(self, modulo_id):
        return next((m for m in self.modulos if m.id == modulo_id), None)

    def pode_acessar(self, modulo_id):
        return any(m.id == modulo_id for m in self.modulos_liberados)

    def ja_concluido(self, modulo_id):
        return any(m.id == modulo_id for m in self.modulos_concluidos)

    def pode_avancar(self, modulo_id):
        proximo = self.proximo_modulo_pendente
        return proximo is not None and proximo.id == modulo_id


def calcular_progresso(modulos, ultimo_modulo_id):
    sequencia = ordenar_modulos(modulos)
    if ultimo_modulo_id is None:
        return EstadoProgresso(sequencia, -1, True)
    indice = next((i for i, m in enumerate(sequencia) if m.id == ultimo_modulo_id), None)
    if indice is None:
        # Não assumir progresso nem resetar um cursor de outro treinamento.
        return EstadoProgresso(sequencia, -1, False)
    return EstadoProgresso(sequencia, indice, True)
