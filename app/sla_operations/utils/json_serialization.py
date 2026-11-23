"""Deterministic JSON serialization utilities for the SLA operations workspace.

This module provides thin, self-contained wrappers around the standard library
``json`` module that guarantee deterministic output by:

* sorting object keys,
* using stable, compact separators,
* encoding ``datetime``/``date``/``time`` values as ISO-8601 strings,
* encoding ``Decimal`` values as strings (to avoid float precision loss),
* encoding ``UUID`` values as strings,
* encoding ``set``/``frozenset`` values as sorted lists.

The module intentionally avoids importing anything from the core application so
that it can be used (and tested) in isolation from maintainer-owned code.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Callable, Optional, Union
from uuid import UUID

__all__ = [
    "DeterministicJSONEncoder",
    "dumps",
    "dumps_pretty",
    "loads",
    "JSONDecodeError",
    "JSONEncodeError",
]

#: Stable separators used for deterministic output (no insignificant whitespace).
_SEPARATORS: tuple[str, str] = (",", ":")

#: Default keyword arguments shared by every ``dumps`` call.
_DEFAULT_DUMPS_KWARGS: dict[str, Any] = {
    "sort_keys": True,
    "separators": _SEPARATORS,
    "ensure_ascii": False,
    "allow_nan": False,
}


class JSONEncodeError(TypeError):
    """Raised when a value cannot be serialized to JSON deterministically."""


class JSONDecodeError(ValueError):
    """Raised when a JSON document cannot be decoded."""


class DeterministicJSONEncoder(json.JSONEncoder):
    """A ``json.JSONEncoder`` that produces deterministic, typed output.

    Non-serializable types are handled explicitly:

    * ``datetime``/``date``/``time`` -> ISO-8601 string
    * ``Decimal`` -> string (preserves precision)
    * ``UUID`` -> string
    * ``set``/``frozenset`` -> sorted list (deterministic ordering)

    Any other unsupported type raises :class:`JSONEncodeError` (a ``TypeError``
    subclass) with a clear message.
    """

    def default(self, o: Any) -> Any:  # noqa: D401 - inherited docstring
        if isinstance(o, (datetime, date, time)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, (set, frozenset)):
            # Sort by string representation to keep ordering deterministic even
            # when the set contains heterogeneous, non-comparable elements.
            return sorted(o, key=lambda item: (type(item).__name__, repr(item)))
        raise JSONEncodeError(
            f"Object of type {type(o).__name__!r} is not JSON serializable"
        )


def dumps(
    obj: Any,
    *,
    indent: Optional[Union[int, str]] = None,
    default: Optional[Callable[[Any], Any]] = None,
    **kwargs: Any,
) -> str:
    """Serialize ``obj`` to a deterministic JSON string.

    Parameters
    ----------
    obj:
        The value to serialize.
    indent:
        Optional indentation for pretty-printing. When ``None`` (the default)
        output is compact. Determinism is preserved in both modes.
    default:
        Optional callable invoked for otherwise non-serializable objects. When
        provided it takes precedence over :class:`DeterministicJSONEncoder`'s
        built-in handling.
    **kwargs:
        Additional keyword arguments forwarded to :func:`json.dumps`. Callers
        may override defaults such as ``sort_keys`` or ``separators``.

    Raises
    ------
    JSONEncodeError
        If ``obj`` contains a value that cannot be serialized.
    """
    call_kwargs: dict[str, Any] = dict(_DEFAULT_DUMPS_KWARGS)
    call_kwargs.update(kwargs)
    call_kwargs["indent"] = indent

    if default is not None:
        call_kwargs["default"] = default
    else:
        call_kwargs["cls"] = DeterministicJSONEncoder

    try:
        return json.dumps(obj, **call_kwargs)
    except JSONEncodeError:
        raise
    except TypeError as exc:
        # Normalize stdlib TypeError into our explicit error type.
        raise JSONEncodeError(str(exc)) from exc


def dumps_pretty(obj: Any, indent: int = 2, **kwargs: Any) -> str:
    """Serialize ``obj`` to a human-readable, deterministic JSON string.

    Convenience wrapper around :func:`dumps` intended for report exports and
    debugging output. Key ordering and separators remain deterministic.

    Parameters
    ----------
    obj:
        The value to serialize.
    indent:
        Number of spaces used for indentation. Must be a non-negative ``int``.
    **kwargs:
        Additional keyword arguments forwarded to :func:`dumps`.

    Raises
    ------
    ValueError
        If ``indent`` is not a non-negative integer.
    JSONEncodeError
        If ``obj`` contains a value that cannot be serialized.
    """
    if isinstance(indent, bool) or not isinstance(indent, int) or indent < 0:
        raise ValueError("indent must be a non-negative integer")
    return dumps(obj, indent=indent, **kwargs)


def loads(s: Union[str, bytes, bytearray]) -> Any:
    """Deserialize a JSON document into Python objects.

    Parameters
    ----------
    s:
        A JSON document as ``str``, ``bytes`` or ``bytearray``.

    Raises
    ------
    JSONDecodeError
        If ``s`` is not valid JSON.
    """
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise JSONDecodeError(str(exc)) from exc