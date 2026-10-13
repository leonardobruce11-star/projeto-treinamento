"""Regra de aprovação pura; não altera progresso, resultados ou participação.

Percentuais manuais admitem int, Decimal ou texto decimal, com até duas
casas significativas após a vírgula decimal. Float é rejeitado para evitar
ambiguidade nos limites. O banco usa Numeric(5, 2), sem default.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class ConfiguracaoAprovacaoInvalida(ValueError):
    """Nota manual fora do contrato de persistência."""


def validar_nota_minima_percentual(valor):
    """Retorna Decimal exato em (0, 100], ou None para regra automática."""
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, Decimal, str)):
        raise ConfiguracaoAprovacaoInvalida(
            "A nota mínima deve ser decimal exato, maior que 0 e até 100."
        )
    try:
        nota = Decimal(valor)
    except (InvalidOperation, ValueError):
        raise ConfiguracaoAprovacaoInvalida("Nota mínima inválida.") from None
    if not nota.is_finite() or not 0 < nota <= 100:
        raise ConfiguracaoAprovacaoInvalida("A nota mínima deve ser maior que 0 e até 100.")
    # Usa a representação exata, sem depender da precisão do contexto Decimal.
    sinal, digitos, expoente = nota.as_tuple()
    coeficiente = int(''.join(map(str, digitos)))
    if expoente < -2:
        # Zeros finais são aceitos; casas adicionais não são arredondadas.
        casas_extras = -expoente - 2
        if casas_extras >= len(digitos) or coeficiente % (10 ** casas_extras):
            raise ConfiguracaoAprovacaoInvalida("A nota mínima admite até duas casas decimais.")
    return nota


def _centesimos(nota):
    _, digitos, expoente = nota.as_tuple()
    coeficiente = int(''.join(map(str, digitos)))
    return (coeficiente * 10 ** (expoente + 2) if expoente >= -2
            else coeficiente // 10 ** (-expoente - 2))


def _percentual_obtido(acertos, total):
    # Arredonda somente a exibição, HALF_UP, usando inteiros.
    centesimos = (2 * acertos * 10000 + total) // (2 * total)
    return Decimal(f"{centesimos // 100}.{centesimos % 100:02d}")


@dataclass(frozen=True)
class AvaliacaoAprovacao:
    total_questoes: int
    percentual_minimo: Decimal | None
    minimo_acertos: int
    total_acertos: int
    percentual: Decimal | None
    aprovado: bool


def avaliar_aprovacao(total_questoes, total_acertos,
                      nota_minima_percentual=None, *, conclusao_explicita=False):
    """Avalia um quiz ou a finalização explícita de conteúdo sem quiz.

    Sem quiz, só (0 questões, 0 acertos) é válido; não há nota percentual e
    aprovado é conclusao_explicita. O chamador deve confirmar previamente a
    conclusão dos módulos: esta função não consulta o banco nem autoriza rotas.
    Com quiz, decide por acertos inteiros, sem comparar percentuais arredondados.
    A elegibilidade/conclusão do conteúdo pertence ao fluxo, não à pontuação.
    """
    if type(total_questoes) is not int or total_questoes < 0:
        raise ValueError("Quantidade de questões deve ser inteiro não negativo.")
    if type(total_acertos) is not int or not 0 <= total_acertos <= total_questoes:
        raise ValueError("Acertos devem ser inteiro entre 0 e a quantidade de questões.")
    if type(conclusao_explicita) is not bool:
        raise ValueError("Conclusão explícita deve ser booleana.")
    nota = validar_nota_minima_percentual(nota_minima_percentual)
    if total_questoes == 0:
        return AvaliacaoAprovacao(0, None, 0, 0, None, conclusao_explicita)
    if nota is None:
        nota = Decimal(80 if total_questoes <= 9 else 70)
    pontos = _centesimos(nota)
    minimo = (total_questoes * pontos + 9999) // 10000
    return AvaliacaoAprovacao(
        total_questoes, nota, minimo, total_acertos,
        _percentual_obtido(total_acertos, total_questoes), total_acertos >= minimo,
    )
