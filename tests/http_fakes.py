"""The streaming surface of a requests response (ok, headers, iter_content,
close) for a test's hand-written fake, built from its own status_code /
raise_for_status / json() / text, so bodies read with `stream=True`
through lib.capped_body see the same reply the fake describes."""
import json as _json


class StreamedBody:
    headers = {}
    closed = False

    @property
    def ok(self):
        if "_ok" in self.__dict__:
            return self._ok
        # requests' own definition: ok unless raise_for_status raises.
        status = getattr(self, "status_code", None)
        if isinstance(status, int):
            return status < 400
        try:
            self.raise_for_status()
        except Exception:
            return False
        return True

    @ok.setter
    def ok(self, value):
        self._ok = value

    def raise_for_status(self):
        pass

    def _streamed_bytes(self) -> bytes:
        text = getattr(self, "text", None)
        if not self.ok and isinstance(text, str):
            return text.encode()
        try:
            return _json.dumps(self.json()).encode()
        except Exception:
            return (text if isinstance(text, str) else "").encode()

    def iter_content(self, size):
        body = self._streamed_bytes()
        for i in range(0, len(body), size):
            yield body[i:i + size]

    def close(self):
        self.closed = True
