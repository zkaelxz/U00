"""Keeps the API's listening ports open: an accept survives a client that vanishes mid-accept, and a listener that closes anyway is opened again."""

import asyncio
import logging
import sys

import uvicorn

# Windows reports a client that went away between connecting and being
# accepted with these codes (ERROR_NETNAME_DELETED, ERROR_SEM_TIMEOUT,
# ERROR_CONNECTION_ABORTED, WSAECONNABORTED, WSAECONNRESET). CPython's
# proactor loop closes the listening socket on any OSError from accept, so
# one such client stopped the port for good while the process lived on.
TRANSIENT_ACCEPT_WINERRORS = frozenset({64, 121, 1236, 10053, 10054})
CHECK_SECONDS = 5.0
# A listener whose accepts keep failing at once (an adapter gone) would
# otherwise spin; past this many in a row the error reaches the loop.
MAX_RETRIES_IN_A_ROW = 100

_log = logging.getLogger("uvicorn.error")


def is_transient_accept_error(exc: BaseException) -> bool:
    return (isinstance(exc, (ConnectionResetError, ConnectionAbortedError))
            or getattr(exc, "winerror", None) in TRANSIENT_ACCEPT_WINERRORS)


class SteadyAcceptMixin:
    """Wraps a proactor's accept(listener): a transient failure asks again
    instead of reaching the loop, which would close the listener. Needs the
    proactor's `_loop` and a base `accept`."""

    def accept(self, listener):
        loop = self._loop  # pyright: ignore[reportAttributeAccessIssue]
        outer = loop.create_future()
        inner_box = []
        retries = [0]

        def attempt():
            inner = super(SteadyAcceptMixin, self).accept(listener)  # pyright: ignore[reportAttributeAccessIssue]
            inner_box[:] = [inner]
            inner.add_done_callback(settle)

        def settle(inner):
            if outer.done():
                return
            if inner.cancelled():
                outer.cancel()
                return
            exc = inner.exception()
            if exc is None:
                outer.set_result(inner.result())
            elif (is_transient_accept_error(exc) and listener.fileno() != -1
                  and retries[0] < MAX_RETRIES_IN_A_ROW):
                retries[0] += 1
                _log.info("A client left before its connection was accepted (%s); still listening.",
                          getattr(exc, "winerror", None) or type(exc).__name__)
                try:
                    attempt()
                except BaseException as again:  # raised in a callback, it would leave the port silent
                    outer.set_exception(again)
            else:
                outer.set_exception(exc)

        def cancel_inner(fut):
            # Once the listener is closed the proactor cancels its own accept;
            # cancelling it here too would fail on the closed handle and log.
            if (fut.cancelled() and inner_box and not inner_box[0].done()
                    and listener.fileno() != -1):
                inner_box[0].cancel()

        outer.add_done_callback(cancel_inner)
        attempt()
        return outer


def new_event_loop() -> asyncio.AbstractEventLoop:
    """The loop the API's servers run on (uvicorn `loop=` and asyncio.run's
    loop_factory). Off Windows, asyncio's own."""
    if sys.platform != "win32":
        return asyncio.new_event_loop()
    from asyncio import windows_events

    class SteadyProactor(SteadyAcceptMixin, windows_events.IocpProactor):
        pass
    return asyncio.ProactorEventLoop(proactor=SteadyProactor())


LOOP = "api.steady_listener:new_event_loop"


def _closed(server) -> bool:
    sockets = server.sockets or ()
    return not sockets or any(s.fileno() == -1 for s in sockets)


class SteadyServer(uvicorn.Server):
    """A uvicorn.Server that checks every CHECK_SECONDS that its listening
    sockets are still open, and opens a closed one again on the same port,
    for a cause the accept retry above does not cover. The running app,
    its jobs and its open connections carry on."""

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        self._addresses = [s.getsockname()[:2] for srv in getattr(self, "servers", [])
                           for s in (srv.sockets or ())]
        self._reopen_failures = 0
        self._reopening = False
        if self.started and self._addresses:
            self._watch_task = asyncio.get_running_loop().create_task(self._watch())

    async def shutdown(self, sockets=None) -> None:
        task = getattr(self, "_watch_task", None)
        if task is not None and not task.done():
            # A reopen cut off mid-way would leave a listener nothing closes,
            # so one in progress is let finish; its server is closed below.
            if not self._reopening:
                task.cancel()
            await asyncio.wait({task}, timeout=CHECK_SECONDS)
            task.cancel()
        await super().shutdown(sockets=sockets)

    async def _watch(self) -> None:
        while not self.should_exit:
            await asyncio.sleep(CHECK_SECONDS)
            if self.should_exit:
                return
            if any(_closed(srv) for srv in self.servers) or self._missing():
                self._reopening = True
                try:
                    await self._reopen()
                finally:
                    self._reopening = False

    def _missing(self) -> bool:
        # A reopen that failed has already dropped the closed server from
        # self.servers, so the addresses are what remember the port.
        open_addresses = {s.getsockname()[:2] for srv in self.servers for s in (srv.sockets or ())}
        return any(address not in open_addresses for address in self._addresses)

    def _protocol(self, _loop=None):
        # The same factory uvicorn's startup builds (uvicorn 0.54, pinned in
        # constraints.txt): a reopened port must share this server's state.
        return self.config.http_protocol_class(  # pyright: ignore[reportCallIssue]
            config=self.config, server_state=self.server_state,  # pyright: ignore[reportCallIssue]
            app_state=self.lifespan.state, _loop=_loop)  # pyright: ignore[reportCallIssue]

    async def _reopen(self) -> None:
        loop = asyncio.get_running_loop()
        kept = [srv for srv in self.servers if not _closed(srv)]
        for srv in self.servers:
            if _closed(srv):
                try:
                    srv.close()
                except (OSError, ValueError):
                    pass   # a selector loop refuses to unregister a socket that is already closed
        open_addresses = {s.getsockname()[:2] for srv in kept for s in (srv.sockets or ())}
        for host, port in self._addresses:
            if (host, port) in open_addresses:
                continue
            try:
                kept.append(await loop.create_server(self._protocol, host=host, port=port,
                                                     ssl=self.config.ssl, backlog=self.config.backlog))
                self._reopen_failures = 0
                _log.error("The listener on %s:%s had closed; it is open again.", host, port)
            except OSError as exc:
                self._reopen_failures += 1
                # First failure, then about once a minute, so a stuck port shows in the log without filling it.
                if self._reopen_failures % 12 == 1:
                    _log.error("The listener on %s:%s closed and can't open again yet (%s); failed %d times, retrying every %s s.",
                               host, port, exc, self._reopen_failures, CHECK_SECONDS)
        self.servers = kept
