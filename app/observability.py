"""Langfuse helpers. PII-masked export; every call degrades to a no-op if keys are missing or Langfuse fails,
so observability can never break or slow down a user turn (export runs in a background thread)."""
import atexit
import contextlib
import logging
from typing import Any

from app import config, security

log = logging.getLogger(__name__)
_lf = None


def langfuse():
    global _lf
    if _lf is None and config.LANGFUSE_ENABLED:
        try:
            from langfuse import Langfuse
            _lf = Langfuse(mask=security.mask_for_logs)  # redact emails/phones in everything exported
            atexit.register(_lf.flush)
        except Exception as e:  # noqa: BLE001
            log.warning("Langfuse disabled: %s", type(e).__name__)
            _lf = False
    return _lf or None


class _Null:
    id = None
    trace_id = None

    def update(self, **_: Any) -> None: ...


@contextlib.contextmanager
def observation(name: str, as_type: str = "span", **kwargs: Any):
    lf = langfuse()
    if lf is None:
        yield _Null()
        return
    try:
        cm = lf.start_as_current_observation(name=name, as_type=as_type, **kwargs)
        obs = cm.__enter__()
    except Exception:  # noqa: BLE001
        yield _Null()
        return
    try:
        yield obs
    finally:
        with contextlib.suppress(Exception):
            cm.__exit__(None, None, None)


@contextlib.contextmanager
def trace_attributes(**kwargs: Any):
    lf = langfuse()
    if lf is None:
        yield
        return
    try:
        from langfuse import propagate_attributes
        cm = propagate_attributes(**kwargs)
        cm.__enter__()
    except Exception:  # noqa: BLE001
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            cm.__exit__(None, None, None)


def score(name: str, value: float) -> None:
    lf = langfuse()
    if lf is not None:
        with contextlib.suppress(Exception):
            lf.score_current_trace(name=name, value=value, data_type="NUMERIC")


def flush() -> None:
    lf = langfuse()
    if lf is not None:
        with contextlib.suppress(Exception):
            lf.flush()
