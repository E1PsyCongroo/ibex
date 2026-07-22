"""Project-specific, user-facing errors."""


class SbflLlmError(RuntimeError):
    """Base error for invalid inputs, model responses, and processing failures."""


class PatchError(SbflLlmError):
    """A patch could not be safely discovered or applied."""


class ModelResponseError(SbflLlmError):
    """The LLM response did not satisfy the required schema or candidate set."""
