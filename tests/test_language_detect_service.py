"""services/language_detect_service.py: which app language a detection result maps to."""
import pytest

from lib.errors import InvalidInputError, UnsupportedOperationError
from services import language_detect_service as lds


def test_a_confident_supported_language_is_used():
    assert lds.pick_language({"language": "th", "probability": 0.93}) == "th"


def test_a_language_the_app_cannot_transcribe_is_refused_by_name():
    with pytest.raises(UnsupportedOperationError):
        lds.pick_language({"language": "fr", "probability": 0.99})


def test_an_unclear_opening_is_refused_instead_of_guessed():
    with pytest.raises(InvalidInputError):
        lds.pick_language({"language": "ja", "probability": 0.3})


def test_it_never_uses_the_turbo_model():
    assert "turbo" not in lds.DETECT_MODEL
