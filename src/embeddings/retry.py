"""
Reusable Retry Policy & Rate Limit Utilities
Location: src/embeddings/retry.py

Provides small, deterministic, and testable retry policies for transient errors.
Supports exponential backoff, bounded jitter, and server-directed Retry-After delay guidance.

Limitations:
- HTTP-date headers in RFC 7231 / RFC 2616 format are parsed relative to UTC.
- Natural-language intervals without numeric values (e.g., "retry in a moment")
  cannot be parsed deterministically and safely fall back to client exponential backoff.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import email.utils
import random
import re
import time
from typing import Any, Callable, Optional, Sequence, Tuple, Type, TypeVar

T = TypeVar("T")


def parse_retry_after(
    source: Any,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> Optional[float]:
    """
    Parses Retry-After guidance from an exception, header string, or numeric value.

    Supported formats:
    1. Numeric seconds (int or float >= 0), e.g. 5, 12.5, "60", "2.5".
    2. HTTP-date strings (RFC 7231 / RFC 2616), e.g. "Wed, 21 Oct 2026 07:28:00 GMT".
       Converted to delta seconds relative to now_fn() (defaulting to datetime.now(timezone.utc)).
    3. Error message substrings:
       - "retry in 15s", "retry after 10.5 seconds", "retry-after: 25"
       - Google/gRPC RetryInfo protobuf string: "retry_delay { seconds: 12 }"
       - "retry_after_seconds: 30"
    4. Exception attributes: .retry_after_s, .retry_after, or response.headers['Retry-After'].

    Returns:
        Float delay in seconds if safely parseable and >= 0, or None if unparseable.
    """
    if source is None:
        return None

    # 1. Direct numeric types
    if isinstance(source, (int, float)) and not isinstance(source, bool):
        return float(source) if source >= 0 else None

    # 2. Exception objects
    if isinstance(source, Exception):
        # Attribute .retry_after_s
        attr_s = getattr(source, "retry_after_s", None)
        if attr_s is not None:
            parsed = parse_retry_after(attr_s, now_fn=now_fn)
            if parsed is not None:
                return parsed

        # Attribute .retry_after
        attr = getattr(source, "retry_after", None)
        if attr is not None:
            parsed = parse_retry_after(attr, now_fn=now_fn)
            if parsed is not None:
                return parsed

        # Response headers if present on HTTP exception
        resp = getattr(source, "response", None)
        if resp is not None and hasattr(resp, "headers") and resp.headers:
            header_val = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
            if header_val is not None:
                parsed = parse_retry_after(header_val, now_fn=now_fn)
                if parsed is not None:
                    return parsed

        headers = getattr(source, "headers", None)
        if headers is not None and isinstance(headers, dict):
            header_val = headers.get("Retry-After") or headers.get("retry-after")
            if header_val is not None:
                parsed = parse_retry_after(header_val, now_fn=now_fn)
                if parsed is not None:
                    return parsed

        # Fall back to inspecting the exception string representation
        return parse_retry_after(str(source), now_fn=now_fn)

    # 3. String values (header value or error text)
    if isinstance(source, str):
        clean = source.strip()
        if not clean:
            return None

        # 3a. Pure numeric string: "120" or "5.5"
        try:
            val = float(clean)
            if val >= 0:
                return val
            return None
        except ValueError:
            pass

        # 3b. HTTP-date: "Wed, 21 Oct 2026 07:28:00 GMT"
        try:
            target_dt = email.utils.parsedate_to_datetime(clean)
            if target_dt is not None:
                if target_dt.tzinfo is None:
                    target_dt = target_dt.replace(tzinfo=timezone.utc)
                now_dt = now_fn() if now_fn is not None else datetime.now(timezone.utc)
                if now_dt.tzinfo is None:
                    now_dt = now_dt.replace(tzinfo=timezone.utc)
                diff = (target_dt - now_dt).total_seconds()
                return max(0.0, float(diff))
        except (TypeError, ValueError):
            pass

        # 3c. Error message regex matches
        # Pattern A: "retry in 15s", "retry after 10.5 seconds", "retry-after: 25"
        m_msg = re.search(
            r"(?:retry (?:in|after)|retry-after:?)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|seconds)?",
            clean,
            re.IGNORECASE,
        )
        if m_msg:
            try:
                val = float(m_msg.group(1))
                if val >= 0:
                    return val
            except (ValueError, TypeError):
                pass

        # Pattern B: Google gRPC RetryInfo "retry_delay { seconds: 12 }"
        m_grpc = re.search(r"retry_delay\s*\{\s*seconds:\s*([0-9]+)", clean, re.IGNORECASE)
        if m_grpc:
            try:
                val = float(m_grpc.group(1))
                if val >= 0:
                    return val
            except (ValueError, TypeError):
                pass

        # Pattern C: "retry_after_seconds: 30"
        m_secs = re.search(r"retry_after_seconds:\s*([0-9]+(?:\.[0-9]+)?)", clean, re.IGNORECASE)
        if m_secs:
            try:
                val = float(m_secs.group(1))
                if val >= 0:
                    return val
            except (ValueError, TypeError):
                pass

    return None


@dataclass(frozen=True)
class RetryPolicy:
    """
    Exponential backoff configuration for transient operational errors.
    
    Attributes:
        max_retries: Maximum number of retry attempts before propagating the error.
        initial_delay_s: Base delay in seconds before the first retry.
        backoff_factor: Multiplier applied to delay after each failure.
        max_delay_s: Maximum delay ceiling in seconds.
        jitter: Whether to add random jitter to mitigate thundering-herd contention.
        jitter_max_s: Upper bound for additive jitter in seconds.
    """
    max_retries: int = 3
    initial_delay_s: float = 0.5
    backoff_factor: float = 2.0
    max_delay_s: float = 10.0
    jitter: bool = False
    jitter_max_s: float = 0.5

    def compute_delay(
        self,
        attempt: int,
        random_fn: Callable[[float, float], float] = random.uniform,
    ) -> float:
        """
        Computes exponential backoff delay for the given attempt (0-indexed).
        Applies ceiling and optional bounded additive jitter.
        """
        raw_delay = self.initial_delay_s * (self.backoff_factor ** attempt)
        capped = min(raw_delay, self.max_delay_s)
        if self.jitter:
            added = random_fn(0.0, self.jitter_max_s)
            return min(capped + added, self.max_delay_s)
        return capped


def execute_with_retry(
    operation: Callable[[], T],
    retryable_exceptions: Tuple[Type[Exception], ...],
    policy: RetryPolicy = RetryPolicy(),
    sleeper: Callable[[float], None] = time.sleep,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> T:
    """
    Executes a callable with exponential backoff on specified exception types.
    Respects server-directed Retry-After delay guidance.

    Safety invariants:
    1. Never retry earlier than a valid server-requested delay.
    2. If a valid server delay exceeds policy.max_delay_s, stop retrying immediately
       and raise the exception rather than retrying early.
    3. Never exceed policy.max_retries attempts.

    Args:
        operation: Zero-argument function to execute.
        retryable_exceptions: Tuple of exception classes that trigger a retry.
        policy: RetryPolicy parameters.
        sleeper: Callable for delay injection. Can be mocked for 0-latency unit tests.
        now_fn: Optional callable returning current datetime for HTTP-date relative calculations.

    Returns:
        The return value of operation().

    Raises:
        The final exception raised by operation() if max_retries is exhausted,
        or immediately if server delay exceeds policy.max_delay_s, or on non-retryable exception.
    """
    attempt = 0
    while True:
        try:
            return operation()
        except retryable_exceptions as exc:
            # If the exception is explicitly marked non-transient, fail fast without retry
            if getattr(exc, "is_transient", True) is False:
                raise exc

            if attempt >= policy.max_retries:
                raise exc

            # Parse server-directed Retry-After guidance
            server_delay = getattr(exc, "retry_after_s", None)
            if server_delay is None:
                server_delay = parse_retry_after(exc, now_fn=now_fn)

            if server_delay is not None and isinstance(server_delay, (int, float)) and server_delay >= 0:
                server_delay = float(server_delay)
                # INVARIANT 1: If server delay exceeds maximum permitted wait ceiling,
                # stop retrying immediately and propagate error; do NOT retry early.
                if server_delay > policy.max_delay_s:
                    raise exc

                # INVARIANT 2: Never retry earlier than the valid server-requested delay.
                computed = policy.compute_delay(attempt)
                delay = max(server_delay, computed)
                if delay > policy.max_delay_s:
                    raise exc
            else:
                delay = policy.compute_delay(attempt)

            sleeper(delay)
            attempt += 1
