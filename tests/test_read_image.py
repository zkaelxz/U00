"""Quick translate's "Read from a screenshot": one picture sent to a vision
engine, the text in it returned. Providers are faked; nothing leaves the test."""
import io
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
PIL = pytest.importorskip("PIL")

from fastapi.testclient import TestClient
from PIL import Image

import db
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from engine_backends import vision
from services import image_text_service, translate_run_service


def _png(size=(40, 20), fmt="PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, fmt)
    return out.getvalue()


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def sent(monkeypatch):
    """Replaces the provider call; records what it was given."""
    calls = []

    def fake(engine, image, mime, language=""):
        calls.append({"engine": engine, "image": image, "mime": mime, "language": language})
        return "你好", {"input_tokens": 300, "output_tokens": 5}

    monkeypatch.setattr(translate_engines, "read_image_text", fake)
    monkeypatch.setattr(image_text_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0.0)
    return calls


def _post(client, image=None, **fields):
    data = {"engine": "claude", **fields}
    return client.post("/api/translate/read-image", data=data, headers={"X-Baihe-Local": "1"},
                       files={"image": ("shot.png", image if image is not None else _png(),
                                        "image/png")})


class TestRoute:
    def test_returns_the_text_and_sends_the_picture(self, client, sent):
        resp = _post(client, language="zh")
        assert resp.status_code == 200 and resp.json() == {"text": "你好"}
        assert sent[0]["mime"] == "image/png" and sent[0]["language"] == "zh"

    def test_paid_call_is_logged(self, client, sent):
        _post(client)
        assert db.get_month_spend() > 0

    def test_nothing_found_says_so(self, client, sent, monkeypatch):
        monkeypatch.setattr(translate_engines, "read_image_text", lambda *a, **k: ("", {}))
        body = _post(client).json()
        assert body["text"] == "" and "No text was found" in body["message"]

    def test_deepseek_is_refused(self, client, sent):
        resp = _post(client, engine="deepseek")
        assert resp.status_code == 400 and "can't read pictures" in resp.text and not sent

    def test_unknown_model_is_refused(self, client, sent):
        assert _post(client, model="claude-made-up-9").status_code == 422 and not sent

    def test_a_non_image_is_refused_before_any_call(self, client, sent):
        resp = _post(client, image=b"not a picture")
        assert resp.status_code == 422 and not sent

    def test_a_gif_is_refused(self, client, sent):
        assert _post(client, image=_png(fmt="GIF")).status_code == 422 and not sent

    def test_a_missing_picture_is_refused(self, client, sent):
        resp = client.post("/api/translate/read-image", data={"engine": "claude"}, headers={"X-Baihe-Local": "1"},
                           files={"other": ("a.txt", b"x", "text/plain")})
        assert resp.status_code == 422

    def test_a_missing_key_is_reported(self, client, sent, monkeypatch):
        monkeypatch.setattr(image_text_service, "resolve_api_key", lambda name, env_path=None: None)
        assert _post(client).status_code == 503 and not sent

    def test_a_spent_cap_stops_a_paid_engine_but_not_a_free_one(self, client, sent, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
        db.log_usage(None, "claude", "claude-sonnet-5-5", "x", 1, 1, 1.0)
        assert _post(client).status_code == 400 and not sent
        assert _post(client, engine="ollama").status_code == 200

    def test_a_picture_that_would_pass_the_cap_is_not_sent(self, client, sent, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0.01)
        db.log_usage(None, "claude", "claude-sonnet-5-5", "x", 1, 1, 0.0099999)
        assert _post(client, image=_png((2000, 2000))).status_code == 400 and not sent

    def test_a_failed_paid_request_is_still_logged(self, client, sent, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("provider down")

        monkeypatch.setattr(translate_engines, "read_image_text", boom)
        assert _post(client).status_code >= 500
        assert db.get_usage_summary()["call_count"] == 1  # zero tokens, but not dropped


class TestEstimate:
    def test_prices_by_size_and_shows_the_cap(self, client, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 5.0)
        resp = client.post("/api/translate/read-image/estimate",
                           json={"engine": "claude", "width": 1280, "height": 720})
        body = resp.json()
        assert resp.status_code == 200
        assert body["estimated_usd"] > 0 and body["cap_left_usd"] == pytest.approx(5.0)
        assert body["fits_cap"] is True

    def test_local_model_is_free(self, client):
        body = client.post("/api/translate/read-image/estimate",
                           json={"engine": "ollama", "width": 800, "height": 200}).json()
        assert body["estimated_usd"] == 0 and body["cap_applies"] is False

    def test_deepseek_is_refused(self, client):
        resp = client.post("/api/translate/read-image/estimate",
                           json={"engine": "deepseek", "width": 10, "height": 10})
        assert resp.status_code == 400


class TestPreparedPicture:
    def test_a_big_picture_is_cut_down(self):
        data, mime, w, h = image_text_service._prepared(_png((4000, 1000)))
        assert (w, h) == (2000, 500) and mime == "image/png"
        assert Image.open(io.BytesIO(data)).size == (2000, 500)

    def test_a_small_picture_is_sent_as_it_came(self):
        original = _png((300, 80))
        assert image_text_service._prepared(original)[0] == original

    def test_webp_is_sent_as_png(self):
        _data, mime, *_ = image_text_service._prepared(_png((300, 80), "WEBP"))
        assert mime == "image/png"


class TestProviders:
    IMAGE = b"\x89PNGdata"

    def test_claude_gets_an_image_block(self):
        seen = {}

        def create(**kw):
            seen.update(kw)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text=" 你好 ")],
                usage=types.SimpleNamespace(input_tokens=11, output_tokens=2))

        engine = types.SimpleNamespace(name="claude", model="m", client=types.SimpleNamespace(
            messages=types.SimpleNamespace(create=create)))
        text, usage = vision.read_image_text(engine, self.IMAGE, "image/png", "zh")
        block = seen["messages"][0]["content"][0]
        assert text == "你好" and usage == {"input_tokens": 11, "output_tokens": 2}
        assert block["type"] == "image" and block["source"]["media_type"] == "image/png"
        assert "Chinese" in seen["messages"][0]["content"][1]["text"]

    def test_gemini_gets_inline_data_and_the_key_in_a_header(self, monkeypatch):
        seen = {}

        def post(url, body, **kw):
            seen.update(url=url, body=body, headers=kw["headers"])
            return {"candidates": [{"content": {"parts": [{"text": "你好"}]}}],
                    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 1}}, None

        monkeypatch.setattr(vision, "post_json", post)
        engine = translate_engines.GeminiEngine("secret-key")
        text, usage = vision.read_image_text(engine, self.IMAGE, "image/jpeg")
        assert text == "你好" and usage["input_tokens"] == 7
        assert "secret-key" not in seen["url"] and seen["headers"]["x-goog-api-key"] == "secret-key"
        assert seen["body"]["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/jpeg"

    def test_openai_gets_a_data_url(self, monkeypatch):
        seen = {}
        engine = translate_engines.OpenAIEngine("k")

        def chat(messages, usage_cb=None):
            seen["messages"] = messages
            usage_cb(9, 3)
            return "你好"

        monkeypatch.setattr(engine, "chat", chat)
        text, usage = vision.read_image_text(engine, self.IMAGE, "image/png")
        part = seen["messages"][0]["content"][1]
        assert text == "你好" and usage == {"input_tokens": 9, "output_tokens": 3}
        assert part["image_url"]["url"].startswith("data:image/png;base64,")

    def test_ollama_gets_the_image_in_images(self, monkeypatch):
        seen = {}

        def chat(base_url, payload):
            seen.update(payload)
            return {"message": {"content": "你好"}, "prompt_eval_count": 5, "eval_count": 2}

        monkeypatch.setattr(vision, "_ollama_chat", chat)
        text, usage = vision.read_image_text(
            translate_engines.OllamaEngine(), self.IMAGE, "image/png")
        assert text == "你好" and usage == {"input_tokens": 5, "output_tokens": 2}
        assert len(seen["messages"][0]["images"]) == 1

    def test_deepseek_is_refused(self):
        with pytest.raises(RuntimeError):
            vision.read_image_text(types.SimpleNamespace(name="deepseek"), self.IMAGE, "image/png")
