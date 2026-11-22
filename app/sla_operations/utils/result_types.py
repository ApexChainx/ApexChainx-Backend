from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Optional, TypeVar, Union

T = TypeVar("T")
U = TypeVar("U")


# ---------------------------------------------------------------------------
# Error code constants
# ---------------------------------------------------------------------------

class ErrorCodes:
    """Common error-code constants for workspace service flows.

    These are plain string constants intended to be used as the ``code``
    argument of :class:`WorkspaceError`. They are grouped in a class purely
    for namespacing; they carry no runtime behavior.
    """

    UNKNOWN = "unknown_error"
    VALIDATION = "validation_error"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    INTERNAL = "internal_error"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"


# ---------------------------------------------------------------------------
# Error container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkspaceError:
    """Immutable description of a failure produced by a workspace flow.

    Parameters
    ----------
    code:
        A short, machine-readable error identifier (e.g. ``"not_found"``).
        Must be a non-empty string after stripping whitespace.
    message:
        A human-readable description of the failure. Must be a non-empty
        string after stripping whitespace.
    details:
        Optional mapping of additional structured context. Stored as a
        read-only copy; ``None`` is normalized to an empty dict.

    Raises
    ------
    ValueError
        If ``code`` or ``message`` is not a string or is empty/whitespace.
    TypeError
        If ``details`` is provided but is not a mapping.
    """

    code: str
    message: str
    details: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("WorkspaceError.code must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("WorkspaceError.message must be a non-empty string")

        details = self.details
        if details is None:
            details = {}
        elif not isinstance(details, dict):
            raise TypeError("WorkspaceError.details must be a dict or None")

        # Store a defensive copy so callers cannot mutate shared state.
        object.__setattr__(self, "details", dict(details))

    def to_dict(self) -> dict:
        """Return a plain-dict representation of the error.

        Returns
        -------
        dict
            ``{"code": ..., "message": ..., "details": {...}}``. The returned
            ``details`` dict is a fresh copy and may be mutated freely.
        """
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Result(Generic[T]):
    """A lightweight success/failure container for workspace service flows.

    A ``Result`` is either a *success* holding a value of type ``T`` or a
    *failure* holding a :class:`WorkspaceError`. Exactly one of the two is
    populated at any time.

    Use :meth:`ok` and :meth:`err` to construct instances rather than the
    dataclass constructor directly.

    Parameters
    ----------
    value:
        The success payload. Present only when ``error`` is ``None``.
    error:
        The failure payload. Present only when ``value`` is ``None``.

    Notes
    -----
    A success may legitimately carry ``None`` as its value (e.g. an operation
    that returns nothing). In that case ``is_success`` is still ``True``
    because the ``error`` slot is empty.
    """

    value: Optional[T] = None
    error: Optional[WorkspaceError] = None

    # -- constructors -------------------------------------------------------

    @classmethod
    def ok(cls, value: Optional[T] = None) -> "Result[T]":
        """Create a successful result.

        Parameters
        ----------
        value:
            The success payload. May be ``None`` for void operations.

        Returns
        -------
        Result[T]
            A result with ``is_success`` set to ``True``.
        """
        return cls(value=value, error=None)

    @classmethod
    def err(
        cls,
        error: Union[WorkspaceError, str],
        message: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> "Result[T]":
        """Create a failed result.

        Parameters
        ----------
        error:
            Either a :class:`WorkspaceError` instance, or a string error code.
        message:
            Required when ``error`` is a string code; ignored otherwise.
        details:
            Optional structured context; only used when ``error`` is a code.

        Returns
        -------
        Result[T]
            A result with ``is_success`` set to ``False``.

        Raises
        ------
        TypeError
            If ``error`` is neither a :class:`WorkspaceError` nor a string.
        ValueError
            If ``error`` is a string code and ``message`` is missing/empty.
        """
        if isinstance(error, WorkspaceError):
            return cls(value=None, error=error)
        if isinstance(error, str):
            if message is None:
                raise ValueError(
                    "message is required when constructing an error from a code"
                )
            return cls(
                value=None,
                error=WorkspaceError(code=error, message=message, details=details),
            )
        raise TypeError(
            "error must be a WorkspaceError or a string code, "
            f"got {type(error).__name__}"
        )

    # -- state --------------------------------------------------------------

    @property
    def is_success(self) -> bool:
        """``True`` when the result holds no error."""
        return self.error is None

    @property
    def is_error(self) -> bool:
        """``True`` when the result holds an error."""
        return self.error is not None

    # -- unwrapping ---------------------------------------------------------

    def unwrap(self) -> T:
        """Return the success value or raise on failure.

        Returns
        -------
        T
            The success payload (possibly ``None`` for void operations).

        Raises
        ------
        ResultError
            If the result is a failure. The raised exception carries the
            underlying :class:`WorkspaceError` as ``.error``.
        """
        if self.error is not None:
            raise ResultError(self.error)
        return self.value  # type: ignore[return-value]

    def unwrap_or(self, default: T) -> T:
        """Return the success value, or ``default`` on failure.

        Parameters
        ----------
        default:
            Fallback value returned when the result is a failure.

        Returns
        -------
        T
            The success payload or ``default``.
        """
        if self.error is not None:
            return default
        return self.value  # type: ignore[return-value]

    def unwrap_or_else(self, factory: Callable[[WorkspaceError], T]) -> T:
        """Return the success value, or compute a fallback from the error.

        Parameters
        ----------
        factory:
            Callable receiving the :class:`WorkspaceError` and returning a
            fallback value. Only invoked on failure.

        Returns
        -------
        T
            The success payload or the value produced by ``factory``.

        Raises
        ------
        TypeError
            If ``factory`` is not callable.
        """
        if not callable(factory):
            raise TypeError("factory must be callable")
        if self.error is not None:
            return factory(self.error)
        return self.value  # type: ignore[return-value]

    def error_or_none(self) -> Optional[WorkspaceError]:
        """Return the error if present, else ``None``."""
        return self.error

    # -- mapping ------------------------------------------------------------

    def map(self, func: Callable[[T], U]) -> "Result[U]":
        """Transform the success value, propagating failures unchanged.

        Parameters
        ----------
        func:
            Callable applied to the success payload.

        Returns
        -------
        Result[U]
            A new result holding ``func(value)`` on success, or the same
            error on failure.

        Raises
        ------
        TypeError
            If ``func`` is not callable.
        """
        if not callable(func):
            raise TypeError("func must be callable")
        if self.error is not None:
            return Result(value=None, error=self.error)
        return Result.ok(func(self.value))  # type: ignore[arg-type]

    def map_error(
        self, func: Callable[[WorkspaceError], WorkspaceError]
    ) -> "Result[T]":
        """Transform the error, propagating successes unchanged.

        Parameters
        ----------
        func:
            Callable receiving the current :class:`WorkspaceError` and
            returning a new :class:`WorkspaceError`.

        Returns
        -------
        Result[T]
            A new result with the transformed error, or the same success.

        Raises
        ------
        TypeError
            If ``func`` is not callable or does not return a
            :class:`WorkspaceError`.
        """
        if not callable(func):
            raise TypeError("func must be callable")
        if self.error is None:
            return Result.ok(self.value)
        new_error = func(self.error)
        if not isinstance(new_error, WorkspaceError):
            raise TypeError("map_error func must return a WorkspaceError")
        return Result(value=None, error=new_error)

    def and_then(self, func: Callable[[T], "Result[U]"]) -> "Result[U]":
        """Chain another result-producing operation on success.

        Parameters
        ----------
        func:
            Callable receiving the success payload and returning a
            ``Result[U]``.

        Returns
        -------
        Result[U]
            The result of ``func(value)`` on success, or the same error on
            failure.

        Raises
        ------
        TypeError
            If ``func`` is not callable or does not return a ``Result``.
        """
        if not callable(func):
            raise TypeError("func must be callable")
        if self.error is not None:
            return Result(value=None, error=self.error)
        result = func(self.value)  # type: ignore[arg-type]
        if not isinstance(result, Result):
            raise TypeError("and_then func must return a Result")
        return result

    # -- helpers ------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain-dict representation of the result.

        Returns
        -------
        dict
            On success: ``{"ok": True, "value": ...}``.
            On failure: ``{"ok": False, "error": {...}}``.
        """
        if self.error is not None:
            return {"ok": False, "error": self.error.to_dict()}
        return {"ok": True, "value": self.value}


# ---------------------------------------------------------------------------
# Exception raised by unwrap()
# ---------------------------------------------------------------------------

class ResultError(Exception):
    """Raised by :meth:`Result.unwrap` when the result is a failure.

    Parameters
    ----------
    error:
        The underlying :class:`WorkspaceError`.

    Attributes
    ----------
    error:
        The wrapped :class:`WorkspaceError`.
    """

    def __init__(self, error: WorkspaceError) -> None:
        if not isinstance(error, WorkspaceError):
            raise TypeError("ResultError requires a WorkspaceError")
        self.error = error
        super().__init__(f"[{error.code}] {error.message}")


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def collect_results(results: Iterable[Result[T]]) -> Result[list]:
    """Combine an iterable of results into a single result of a list.

    Parameters
    ----------
    results:
        An iterable of :class:`Result` instances.

    Returns
    -------
    Result[list]
        ``Result.ok([...])`` with all success values in order if every input
        succeeded. Otherwise ``Result.err(...)`` with the first encountered
        error (input order preserved).

    Raises
    ------
    TypeError
        If any element is not a :class:`Result`.
    """
    values: list = []
    for item in results:
        if not isinstance(item, Result):
            raise TypeError(
                f"collect_results expects Result instances, got {type(item).__name__}"
            )
        if item.error is not None:
            return Result(value=None, error=item.error)
        values.append(item.value)
    return Result.ok(values)


def first_error(results: Iterable[Result[T]]) -> Optional[WorkspaceError]:
    """Return the first error found in ``results``, or ``None``.

    Parameters
    ----------
    results:
        An iterable of :class:`Result` instances.

    Returns
    -------
    Optional[WorkspaceError]
        The first error encountered in input order, or ``None`` if all
        results succeeded (or the iterable was empty).

    Raises
    ------
    TypeError
        If any element is not a :class:`Result`.
    """
    for item in results:
        if not isinstance(item, Result):
            raise TypeError(
                f"first_error expects Result instances, got {type(item).__name__}"
            )
        if item.error is not None:
            return item.error
    return None


__all__ = [
    "ErrorCodes",
    "WorkspaceError",
    "Result",
    "ResultError",
    "collect_results",
    "first_error",
]