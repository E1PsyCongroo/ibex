"""Errors raised while parsing shared SBFL artifacts."""


class SbflCommonError(RuntimeError):
    """Invalid or missing shared SBFL artifact data."""


class SummaryError(SbflCommonError):
    """Invalid summary or rerank result data."""
