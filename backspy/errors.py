"""Exceptions raised by backspy."""


class BackspaceError(Exception):
    """Base class for everything backspy raises."""


class HTTPException(BackspaceError):
    """A REST call returned a 4xx/5xx answer."""

    def __init__(self, status, method, path, payload=None):
        self.status = status
        self.method = method
        self.path = path
        self.payload = payload if isinstance(payload, dict) else {}
        self.code = self.payload.get("code")
        self.details = self.payload.get("details")
        message = self.payload.get("error") or self.payload.get("message") or "HTTP error"
        suffix = f" code={self.code}" if self.code else ""
        super().__init__(f"{status} {method} {path}: {message}{suffix}")


class RateLimited(HTTPException):
    """Still rate limited after retries."""

    def __init__(self, status, method, path, payload=None, *, retry_after=None):
        super().__init__(status, method, path, payload)
        self.retry_after = retry_after


class LoginFailure(BackspaceError):
    """The token was rejected."""


class ConnectionClosed(BackspaceError):
    """A websocket action was attempted with no live connection."""