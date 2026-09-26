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


def decompress_from_base64(data: str) -> str:
    if data is None:
        return ""
    if data == "":
        return None
    try:
        values = [_B64_INDEX[c] for c in data]
    except KeyError as e:
        raise ValueError(f"Not LZString Base64 data (bad character {e.args[0]!r})") from None
    return _decompress(len(values), 32, lambda i: values[i])


def _decompress(length: int, reset_value: int, get_next_value) -> str:
    dictionary = {0: 0, 1: 1, 2: 2}
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
        result.append(entry)

        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1


def compress_to_base64(text: str) -> str:
    """The inverse -- only used to build test fixtures, so tests can
    assert a real round trip instead of hand-copied opaque strings."""
    out = _compress(text, 6, lambda a: _B64[a])
    return out + "=" * ((4 - len(out) % 4) % 4)


def _compress(uncompressed: str, bits_per_char: int, get_char) -> str:
    if uncompressed is None:
        return ""
    dictionary = {}
    to_create = set()
    w = ""
    enlarge_in = 2
    dict_size = 3
    num_bits = 2
    data = []
    data_val = 0
    data_position = 0

    def emit_bits(value, n, lsb_first=True):
        nonlocal data_val, data_position
        for _ in range(n):
            data_val = (data_val << 1) | (value & 1)
            if data_position == bits_per_char - 1:
                data_position = 0
                data.append(get_char(data_val))
                data_val = 0
            else:
                data_position += 1
            value >>= 1

    def output_w():
        nonlocal enlarge_in, num_bits
        if w in to_create:
            code = ord(w[0])
            if code < 256:
                emit_bits(0, num_bits)
                emit_bits(code, 8)
            else:
                emit_bits(1, num_bits)
                emit_bits(code, 16)
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 1 << num_bits
                num_bits += 1
            to_create.discard(w)
        else:
            emit_bits(dictionary[w], num_bits)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

    for c in uncompressed:
        if c not in dictionary:
            dictionary[c] = dict_size
            dict_size += 1
            to_create.add(c)
        wc = w + c
        if wc in dictionary:
            w = wc
        else:
            output_w()
            dictionary[wc] = dict_size
            dict_size += 1
            w = c

    if w != "":
        output_w()

    emit_bits(2, num_bits)

    while True:
        data_val <<= 1
        if data_position == bits_per_char - 1:
            data.append(get_char(data_val))
            break
        data_position += 1
    return "".join(data)
