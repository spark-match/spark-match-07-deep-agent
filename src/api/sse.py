"""Keep-alive for the AG-UI SSE stream.

CloudFront sits in front of the agent's ALB (``modules/agent-service`` in
spark-match-02-infrastructure) with ``origin_read_timeout = 60``, which is
the maximum AWS allows without a quota increase. That timeout counts the
gap *between bytes*, not the total length of the response, so a stream
that keeps emitting is never at risk no matter how long the whole turn
takes.

The risk is the gaps. ``RUN_STARTED`` goes out immediately, but what comes
next is not a token: it is ``GuardrailsMiddleware``, then a full Haiku
classification call in ``ContentFilterMiddleware``, then the main model's
time-to-first-token with the whole system prompt and hydrated profile in
context, and — on turns that use tools — a web search or a subagent that
can run for a while before producing anything streamable. Any one of
those can exceed 60 seconds on a bad day. When it does, CloudFront closes
the connection mid-turn and the student sees the answer stop halfway,
with no error anywhere: the origin is still happily generating into a
socket nobody is reading.

Emitting a comment line every few seconds keeps the byte clock reset.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

# A line starting with ':' is a comment per the SSE specification. Every
# conforming client drops it without dispatching an event -- EventSource
# and a hand-written `fetch` + ReadableStream parser alike -- so this is
# invisible to the AG-UI protocol while still being bytes on the wire,
# which is the only thing the intermediaries are counting.
SSE_COMMENT_PING = ": ping\n\n"


async def with_heartbeat(
    source: AsyncIterator[str],
    interval_seconds: float,
) -> AsyncIterator[str]:
    """Yield everything ``source`` yields, injecting a ping when it goes quiet.

    The ping is only emitted when nothing real arrived within
    ``interval_seconds``, so a stream that is actively producing tokens
    never pays for it. A non-positive interval disables the keep-alive
    and reduces this to a passthrough.
    """
    if interval_seconds <= 0:
        async for chunk in source:
            yield chunk
        return

    iterator = source.__aiter__()
    # Held across loop iterations so a pending read survives the timeouts
    # that produce pings: re-issuing ``__anext__`` each time would be a
    # protocol error on the underlying generator.
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())

            done, _ = await asyncio.wait({pending}, timeout=interval_seconds)
            if not done:
                yield SSE_COMMENT_PING
                continue

            try:
                chunk = pending.result()
            finally:
                # Cleared before the yield below so the teardown path can
                # tell "a read is in flight" from "the read completed".
                pending = None
            yield chunk
    except StopAsyncIteration:
        return
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
        # The consumer closing us (client disconnect, most often) has to
        # close the agent run too. Without this the source generator stays
        # suspended at its yield until the event loop's async-generator
        # finalizer gets to it, which on a long-running server means the
        # LangGraph run leaks for the lifetime of the process.
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()


__all__ = ["SSE_COMMENT_PING", "with_heartbeat"]
