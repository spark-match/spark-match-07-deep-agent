"""Lo unico del agente que habla con spark-match-03-backend por HTTP."""

from src.backend.reports_client import (
    CODIGO_PERFIL_CORTO,
    CODIGO_SIN_RIASEC,
    CODIGO_TOPE_DIARIO,
    CODIGO_YA_EN_CURSO,
    BackendNoConfigurado,
    ErrorDelBackend,
    complete_report,
    fail_report,
    open_report,
)

__all__ = [
    "CODIGO_PERFIL_CORTO",
    "CODIGO_SIN_RIASEC",
    "CODIGO_TOPE_DIARIO",
    "CODIGO_YA_EN_CURSO",
    "BackendNoConfigurado",
    "ErrorDelBackend",
    "complete_report",
    "fail_report",
    "open_report",
]
