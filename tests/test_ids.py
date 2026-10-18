import pytest

from lib import ids
from lib.errors import InvalidInputError


@pytest.mark.parametrize("value", [1, 7, 2**31 - 1])
def test_valid_ids(value):
    assert ids.is_id(value)
    assert ids.check_id(value, "bad") == value


@pytest.mark.parametrize("value", [True, False, 0, -1, 2**31, "1", 1.0, None])
def test_invalid_ids_raise_the_given_message(value):
    assert not ids.is_id(value)
    with pytest.raises(InvalidInputError) as exc:
        ids.check_id(value, "A title id is a positive whole number.")
    assert exc.value.message == "A title id is a positive whole number."


def test_range_message_only_for_whole_numbers_out_of_range():
    for value, expected in ((0, "range"), (2**31, "range"), (True, "type"), ("1", "type")):
        with pytest.raises(InvalidInputError) as exc:
            ids.check_id(value, "type", range_message="range")
        assert exc.value.message == expected
