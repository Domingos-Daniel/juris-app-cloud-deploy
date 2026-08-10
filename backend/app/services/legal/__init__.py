from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "legal_classifier": ("app.services.legal.classification", "legal_classifier"),
    "legal_composer": ("app.services.legal.composition", "legal_composer"),
    "legal_confidence_service": (
        "app.services.legal.confidence",
        "legal_confidence_service",
    ),
    "legal_retrieval_service": (
        "app.services.legal.retrieval",
        "legal_retrieval_service",
    ),
    "legal_validation_service": (
        "app.services.legal.validation",
        "legal_validation_service",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
