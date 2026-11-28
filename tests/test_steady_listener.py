"""The API's listening ports stay open: a client that vanishes mid-accept,
or a listener closed some other way, must not leave the port dead."""

import asyncio
import pytest
import uvicorn

from api import steady_listener


def _winerror(code):
    exc = OSError(22, "The specified network name is no longer available")
    setattr(exc, "winerror", code)
    return exc


class _FakeProactor:
    """A base proactor whose accepts fail or succeed in the order given."""

    def __init__(self, loop, outcomes):
        self._loop = loop
        self.outcomes = list(outcomes)
        self.issued = []

    def accept(self, listener):
        fut = self._loop.create_future()
        self.issued.append(fut)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, tuple) and outcome[0] == "raise":
            raise outcome[1]
        if outcome == "pending":
            return fut
        if isinstance(outcome, BaseException):
            self._loop.call_soon(fut.set_exception, outcome)
        else:
            self._loop.call_soon(fut.set_result, outcome)
        return fut


class _Steady(steady_listener.SteadyAcceptMixin, _FakeProactor):
    pass


class _Listener:
    def __init__(self, open_=True):
        self.open = open_

    def fileno(self):
        return 3 if self.open else -1


def test_a_client_that_left_mid_accept_does_not_end_the_accept():
    async def go():
        loop = asyncio.get_running_loop()
        proactor = _Steady(loop, [_winerror(64), ConnectionResetError(), ("conn", "addr")])
        assert await proactor.accept(_Listener()) == ("conn", "addr")
        assert len(proactor.issued) == 3
    asyncio.run(go())


def test_a_real_accept_failure_still_reaches_the_loop():
    async def go():
        loop = asyncio.get_running_loop()
        proactor = _Steady(loop, [_winerror(10022)])
        with pytest.raises(OSError):
            await proactor.accept(_Listener())
    asyncio.run(go())


def test_a_closed_listener_is_not_asked_again():
    async def go():
        loop = asyncio.get_running_loop()
        proactor = _Steady(loop, [_winerror(64), ("never", "asked")])
        with pytest.raises(OSError):
            await proactor.accept(_Listener(open_=False))
        assert len(proactor.issued) == 1
    asyncio.run(go())


def test_an_error_raised_by_the_retry_itself_still_reaches_the_loop():
    async def go():
        loop = asyncio.get_running_loop()
        proactor = _Steady(loop, [_winerror(64), ("raise", OSError(24, "too many"))])
        with pytest.raises(OSError, match="too many"):
            await asyncio.wait_for(proactor.accept(_Listener()), 1)
    asyncio.run(go())


def test_an_accept_that_keeps_failing_at_once_stops_retrying(monkeypatch):
    monkeypatch.setattr(steady_listener, "MAX_RETRIES_IN_A_ROW", 3)

    async def go():
        loop = asyncio.get_running_loop()
        proactor = _Steady(loop, [_winerror(64)] * 4 + [("never", "asked")])
        with pytest.raises(OSError):
            await proactor.accept(_Listener())
        assert len(proactor.issued) == 4
    asyncio.run(go())


def test_cancelling_the_accept_cancels_the_pending_one():
    async def go():
        loop = asyncio.get_running_loop()
        proactor = _Steady(loop, ["pending"])
        outer = proactor.accept(_Listener())
        outer.cancel()
        await asyncio.sleep(0)
        assert proactor.issued[0].cancelled()
    asyncio.run(go())


async def _ok_app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def _get(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    await writer.drain()
    data = await reader.read()
    writer.close()
    return data


def test_a_listener_that_closed_is_opened_again_on_the_same_port(monkeypatch):
    monkeypatch.setattr(steady_listener, "CHECK_SECONDS", 0.05)

    async def go():
        server = steady_listener.SteadyServer(uvicorn.Config(
            _ok_app, host="127.0.0.1", port=0, log_level="critical", lifespan="on"))
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        assert b" 200 " in await _get(port)
        # What the proactor does on an accept error: the listening socket closes.
        loop = asyncio.get_running_loop()
        for s in server.servers[0].sockets:
            loop.remove_reader(s.fileno())
            getattr(s, "_sock").close()
        with pytest.raises(OSError):
            await _get(port)
        for _ in range(100):
            await asyncio.sleep(0.05)
            try:
                if b" 200 " in await _get(port):
                    break
            except OSError:
                continue
        else:
            pytest.fail("the port did not open again")
        server.should_exit = True
        await task
    asyncio.run(go())


def test_a_failed_reopen_is_tried_again(monkeypatch):
    monkeypatch.setattr(steady_listener, "CHECK_SECONDS", 0.05)

    async def go():
        server = steady_listener.SteadyServer(uvicorn.Config(
            _ok_app, host="127.0.0.1", port=0, log_level="critical", lifespan="on"))
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_running_loop()
        real, failures = loop.create_server, []

        async def flaky(*args, **kwargs):
            if len(failures) < 2:
                failures.append(1)
                raise OSError("port busy")
            return await real(*args, **kwargs)
        loop.create_server = flaky
        for s in server.servers[0].sockets:
            loop.remove_reader(s.fileno())
            getattr(s, "_sock").close()
        for _ in range(100):
            await asyncio.sleep(0.05)
            try:
                if b" 200 " in await _get(port):
                    break
            except OSError:
                continue
        else:
            pytest.fail("the port was not tried again after a failed reopen")
        assert len(failures) == 2
        server.should_exit = True
        await task
    asyncio.run(go())


def test_new_event_loop_is_a_working_loop():
    loop = steady_listener.new_event_loop()
    try:
        assert loop.run_until_complete(asyncio.sleep(0, result=7)) == 7
    finally:
        loop.close()
