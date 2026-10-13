"""Formatação compartilhada de duração; não calcula tempo de atividade."""


def formatar_tempo_ativo(segundos):
    segundos = max(0, int(segundos or 0))
    if segundos == 0:
        return "0 min"
    horas, resto = divmod(segundos, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        # Exibição em horas omite segundos, sem arredondar o total armazenado.
        return f"{horas} h {minutos:02d} min"
    if minutos:
        return f"{minutos} min {segundos} s" if segundos else f"{minutos} min"
    return f"{segundos} s"
