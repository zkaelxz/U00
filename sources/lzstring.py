"""
sources/lzstring.py -- LZString's Base64 variant (pieroxy/lz-string,
MIT), which some sites use to compress data their own page JS unpacks.

Reimplemented here instead of depending on the `lzstring` PyPI package:
that package's last release is from 2018, ships only as an sdist, and
fails to build under current setuptools -- a broken install is worse
than ~100 lines of well-known algorithm. Verified against the reference
JS implementation (see tests/test_sources_manhuagui.py).

This is a compression format, not encryption: decoding it is the same
thing the page's own script does for every visitor.
"""

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_B64_INDEX = {c: i for i, c in enumerate(_B64)}

# LZ78 output can grow quadratically in its input (18 KB of crafted data
# expands to 40 MB), and the input is whatever a page sends. The largest
# real payload, manhuagui's hidden chapter list for a 1,000+ chapter
# series, is roughly 250k characters; this is about four times that.
MAX_OUTPUT_CHARS = 1_000_000


class LZStringTooLarge(ValueError):
    """Valid data whose output passes the size limit, so callers can say
    "too large" rather than "unreadable"."""


def decompress_from_base64(data: str | None, max_chars: int = MAX_OUTPUT_CHARS) -> str | None:
    if data is None:
        return ""
    if data == "":
        return None
    try:
        values = [_B64_INDEX[c] for c in data]
    except KeyError as e:
        raise ValueError(f"Not LZString Base64 data (bad character {e.args[0]!r})") from None
    return _decompress(len(values), 32, lambda i: values[i], max_chars)


def _decompress(length: int, reset_value: int, get_next_value, max_chars: int) -> str:
    # Codes 0-2 are control codes (char8, char16, end), handled before any
    # lookup, so they need no entries.
    dictionary: dict[int, str] = {}
    enlarge_in = 4
    dict_size = 4
    num_bits = 3
    result = []

    data_val = get_next_value(0)
    data_position = reset_value
    data_index = 1

    def read_bits(n):
        nonlocal data_val, data_position, data_index
        bits = 0
        max_power = 1 << n
        power = 1
        while power != max_power:
            resb = data_val & data_position
            data_position >>= 1
            if data_position == 0:
                data_position = reset_value
                data_val = get_next_value(data_index) if data_index < length else 0
                data_index += 1
            if resb > 0:
                bits |= power
            power <<= 1
        return bits

    nxt = read_bits(2)
    if nxt == 0:
        c = chr(read_bits(8))
    elif nxt == 1:
        c = chr(read_bits(16))
    else:
        return ""
    dictionary[3] = c
    w = c
    result.append(c)
    total = 1

    while True:
        if data_index > length:
            return ""
        cc = read_bits(num_bits)
        if cc == 0:
            dictionary[dict_size] = chr(read_bits(8))
            dict_size += 1
            cc = dict_size - 1
            enlarge_in -= 1
        elif cc == 1:
            dictionary[dict_size] = chr(read_bits(16))
            dict_size += 1
            cc = dict_size - 1
            enlarge_in -= 1
        elif cc == 2:
            return "".join(result)

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

        if cc in dictionary:
            entry = dictionary[cc]
        elif cc == dict_size:
            entry = w + w[0]
        else:
            raise ValueError("Invalid LZString data")
        total += len(entry)
        if total > max_chars:
            raise LZStringTooLarge("LZString data decompresses past the size limit")
        result.append(entry)

        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1
