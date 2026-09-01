class RazorpayError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class RazorpayAuthError(RazorpayError):
    def __init__(self, description: str = "authentication failed") -> None:
        super().__init__(f"razorpay auth error: {description}")


class RazorpayRateLimitError(RazorpayError):
    def __init__(self, status_code: int, code: str, description: str, retry_after: float | None) -> None:
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"razorpay rate limited ({code}): {description}")


class RazorpayServerError(RazorpayError):
    def __init__(self, status_code: int | None, description: str) -> None:
        self.status_code = status_code
        super().__init__(f"razorpay server error ({status_code}): {description}")


class RazorpayRequestError(RazorpayError):
    def __init__(self, status_code: int, code: str, description: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(f"razorpay request error {status_code} ({code}): {description}")
