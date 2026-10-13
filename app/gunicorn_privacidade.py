"""Preserva os erros do Gunicorn com sanitização antes da emissão."""
from gunicorn.glogging import Logger
from log_privacidade import proteger_logger


class LoggerPrivacidade(Logger):
    def setup(self, cfg):
        super().setup(cfg)
        proteger_logger(self.error_log)
        proteger_logger(self.access_log)
