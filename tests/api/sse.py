"""Unit tests for the SSE keep-alive wrapper (src/api/sse.py).

The production failure this guards against is invisible from inside the
agent: CloudFront closes an origin connection after 60 seconds without
bytes, so a turn that thinks for too long before its first token gets
truncated mid-stream while the origin keeps generating into a socket
nobody reads. Timings here are compressed to milliseconds — what is being
verified is the behaviour (a ping appears in a gap, and only in a gap),
not the production interval.
"""

import asyncio

import pytest

from src.api.sse import SSE_COMMENT_PING, with_heartbeat


async def _drain(source, interval):
    return [chunk async for chunk in with_heartbeat(source, interval)]


async def _immediate(*chunks):
    for chunk in chunks:
        yield chunk


class TestPassthrough:
    async def test_yields_every_chunk_in_order(self):
        result = await _drain(_immediate("a", "b", "c"), 10.0)

        assert result == ["a", "b", "c"]

    async def test_fast_stream_never_pings(self):
        """A stream that keeps producing pays nothing for the keep-alive."""
        result = await _drain(_immediate("a", "b"), 10.0)

        assert SSE_COMMENT_PING not in result

    async def test_empty_stream_yields_nothing(self):
        result = await _drain(_immediate(), 10.0)

        assert result == []

    async def test_non_positive_interval_disables_the_keepalive(self):
        async def slow():
            await asyncio.sleep(0.05)
            yield "a"

        result = await _drain(slow(), 0)

        assert result == ["a"]


class TestHeartbeat:
    async def test_ping_fills_a_silent_gap(self):
        async def stalls_then_answers():
            await asyncio.sleep(0.12)
            yield "answer"

        result = await _drain(stalls_then_answers(), 0.02)

        assert result[-1] == "answer"
        assert result.count(SSE_COMMENT_PING) >= 1
        assert set(result[:-1]) == {SSE_COMMENT_PING}

    async def test_ping_is_an_sse_comment(self):
        """Clients must drop it without dispatching an AG-UI event, which
        the SSE spec guarantees only for lines starting with ':'."""
        assert SSE_COMMENT_PING.startswith(":")
        assert SSE_COMMENT_PING.endswith("\n\n")

    async def test_keeps_pinging_across_multiple_gaps(self):
        async def two_gaps():
            await asyncio.sleep(0.08)
            yield "first"
            await asyncio.sleep(0.08)
            yield "second"

        result = await _drain(two_gaps(), 0.02)

        assert [c for c in result if c != SSE_COMMENT_PING] == ["first", "second"]
        # A ping before each real chunk, not just before the first one.
        assert result.index("first") > 0
        assert result.index("second") > result.index("first") + 1


class TestTeardown:
    async def test_closes_the_source_when_the_consumer_stops_early(self):
        """Client disconnect must tear down the agent run. Leaving the
        source suspended at its yield leaks it until the event loop's
        async-generator finalizer runs, which on a long-lived server is
        effectively never."""
        closed = False

        async def source():
            nonlocal closed
            try:
                yield "a"
                yield "b"
            finally:
                closed = True

        stream = with_heartbeat(source(), 10.0)
        assert await stream.__anext__() == "a"
        await stream.aclose()

        assert closed is True

    async def test_closes_the_source_while_a_read_is_in_flight(self):
        """Same, but disconnecting mid-gap: the pending __anext__ has to be
        cancelled before the source can be closed, or teardown hangs."""
        closed = False

        async def never_answers():
            nonlocal closed
            try:
                await asyncio.sleep(3600)
                yield "unreachable"
            finally:
                closed = True

        stream = with_heartbeat(never_answers(), 0.02)
        assert await stream.__anext__() == SSE_COMMENT_PING

        await asyncio.wait_for(stream.aclose(), timeout=2.0)

        assert closed is True

    async def test_propagates_source_exceptions(self):
        async def explodes():
            yield "a"
            raise RuntimeError("agent blew up mid-turn")

        with pytest.raises(RuntimeError, match="blew up"):
            await _drain(explodes(), 10.0)
