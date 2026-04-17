"""공통 예외"""


class AppError(Exception):
    def __init__(self, message: str, code: str = "UNKNOWN"):
        self.message = message
        self.code = code
        super().__init__(message)


class TimeoutError(AppError):
    def __init__(self, message: str = "Task timed out"):
        super().__init__(message, "TIMEOUT")


class OverloadError(AppError):
    def __init__(self, message: str = "System overloaded"):
        super().__init__(message, "OVERLOAD")


class ValidationError(AppError):
    def __init__(self, message: str = "Validation failed"):
        super().__init__(message, "VALIDATION")


class LLMError(AppError):
    def __init__(self, message: str = "LLM call failed"):
        super().__init__(message, "LLM_ERROR")


class CircuitOpenError(AppError):
    def __init__(self, message: str = "Circuit breaker open"):
        super().__init__(message, "CIRCUIT_OPEN")
