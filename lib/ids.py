"""Id validation shared by the services: a client-sent row id is a whole
number from 1 to 2**31 - 1. SQLite ints are 64-bit and a larger Python int
raises OverflowError (a 500), so ids are capped well below that."""

from lib.errors import InvalidInputError

MAX_ID = 2**31 - 1


def is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_ID


def check_id(value, message: str, *, range_message: str = None):
    """Returns `value` when it is an id, else raises InvalidInputError with
    `message`. With `range_message`, a whole number outside 1..MAX_ID gets that
    text instead (callers whose existing errors tell the two apart)."""
    if range_message is not None and isinstance(value, int) and not isinstance(value, bool):
        if not 1 <= value <= MAX_ID:
            raise InvalidInputError(range_message)
        return value
    if not is_id(value):
        raise InvalidInputError(message)
    return value


def check_id_of(what: str, value):
    """check_id with the "A <what> id is a positive whole number." wording."""
    return check_id(value, f"A {what} id is a positive whole number.")


def check_id_field(name: str, value):
    """check_id for a named request field, telling a non-number from one out of
    range."""
    return check_id(value, f"{name} must be a whole number.",
                    range_message=f"{name} is out of range.")
