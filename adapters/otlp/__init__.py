"""Read-only OpenTelemetry OTLP/JSON recording adapter."""

from .importer import OTLPImportError, import_otlp, looks_like_otlp_export

__all__ = ["OTLPImportError", "import_otlp", "looks_like_otlp_export"]
