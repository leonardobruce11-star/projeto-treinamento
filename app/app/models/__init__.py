from .usuario import Usuario
from .departamento import Departamento
from .colaborador import Colaborador
from .campanha import Campanha
from .auditoria import Auditoria
from .grupo import Grupo
from .grupo_participante import GrupoParticipante
from .cenario import Cenario
from .template_email import TemplateEmail
from .landing_page import LandingPage
from .categoria_treinamento import CategoriaTreinamento
from .treinamento import Treinamento
from .modulo_treinamento import ModuloTreinamento
from .participacao_treinamento import ParticipacaoTreinamento
from .questao import Questao
from .alternativa import Alternativa
from .resultado_quiz import ResultadoQuiz
from .tempo_treinamento import TempoTreinamento
from .feedback_treinamento import FeedbackTreinamento
from .envio_convite_treinamento import EnvioConviteTreinamento
from .tentativa_envio_convite import TentativaEnvioConvite

# Registra a proteção estrutural para operações ORM de módulos.
from app.utils import estrutura_treinamento  # noqa: F401

from .confirmacao_convites import ConfirmacaoConvites

from .simulacao import (RevisaoSimulacao, ExecucaoSimulacao, AlvoSimulacao,
                        RecursoExternoSimulacao, OperacaoSimulacao)

from .simulacao_resultados import VinculoCampanhaSimulacao, EventoSimulacao, AssociacaoEducacionalSimulacao, PonteEducativaSimulacao, LeaseOperacaoSimulacao

from .submissao_quiz import SubmissaoQuiz

from .certificado_treinamento import CertificadoTreinamento
