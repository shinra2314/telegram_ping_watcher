from __future__ import annotations

from pathlib import Path


_DATA_CODEWORDS_L = {1: 19, 2: 34, 3: 55, 4: 80, 5: 108}
_ECC_CODEWORDS_L = {1: 7, 2: 10, 3: 15, 4: 20, 5: 26}
_ALIGNMENT_POSITIONS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
}
_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7}


def qr_matrix(text: str) -> list[list[bool]]:
    data = text.encode("utf-8")
    version = _choose_version(len(data))
    size = 21 + (version - 1) * 4
    modules: list[list[bool | None]] = [[None] * size for _ in range(size)]
    function_modules = [[False] * size for _ in range(size)]

    def set_function(x: int, y: int, value: bool) -> None:
        modules[y][x] = value
        function_modules[y][x] = True

    _draw_function_patterns(version, modules, function_modules, set_function)
    data_codewords = _make_data_codewords(data, version)
    ecc = _reed_solomon_remainder(data_codewords, _ECC_CODEWORDS_L[version])
    bits = _bytes_to_bits(data_codewords + ecc)
    bits.extend([0] * _REMAINDER_BITS[version])
    _draw_codewords(modules, function_modules, bits, mask=0)
    _draw_format_bits(modules, function_modules, mask=0)

    return [[bool(cell) for cell in row] for row in modules]


def terminal_qr(text: str, quiet_zone: int = 2) -> str:
    matrix = qr_matrix(text)
    width = len(matrix) + quiet_zone * 2
    white = "  "
    black = "██"
    rows = [white * width for _ in range(quiet_zone)]
    for row in matrix:
        rendered = [white] * quiet_zone
        rendered.extend(black if cell else white for cell in row)
        rendered.extend([white] * quiet_zone)
        rows.append("".join(rendered))
    rows.extend([white * width for _ in range(quiet_zone)])
    return "\n".join(rows)


def write_svg_qr(text: str, path: Path, scale: int = 10, quiet_zone: int = 4) -> Path:
    matrix = qr_matrix(text)
    size = len(matrix)
    canvas = (size + quiet_zone * 2) * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}" viewBox="0 0 {canvas} {canvas}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
    ]
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell:
                rx = (x + quiet_zone) * scale
                ry = (y + quiet_zone) * scale
                parts.append(f'<rect x="{rx}" y="{ry}" width="{scale}" height="{scale}" fill="#000"/>')
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def _choose_version(byte_length: int) -> int:
    for version, capacity in _DATA_CODEWORDS_L.items():
        usable_bytes = (capacity * 8 - 12) // 8
        if byte_length <= usable_bytes:
            return version
    raise ValueError("QR payload is too long for the built-in QR generator")


def _make_data_codewords(data: bytes, version: int) -> list[int]:
    capacity = _DATA_CODEWORDS_L[version]
    bits: list[int] = []
    _append_bits(bits, 0b0100, 4)
    _append_bits(bits, len(data), 8)
    for value in data:
        _append_bits(bits, value, 8)
    terminator = min(4, capacity * 8 - len(bits))
    bits.extend([0] * terminator)
    while len(bits) % 8:
        bits.append(0)
    codewords = [int("".join(str(bit) for bit in bits[i : i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < capacity:
        codewords.append(pad)
        pad = 0x11 if pad == 0xEC else 0xEC
    return codewords


def _append_bits(bits: list[int], value: int, count: int) -> None:
    for i in range(count - 1, -1, -1):
        bits.append((value >> i) & 1)


def _bytes_to_bits(values: list[int]) -> list[int]:
    bits: list[int] = []
    for value in values:
        _append_bits(bits, value, 8)
    return bits


def _draw_function_patterns(version, modules, function_modules, set_function) -> None:
    size = len(modules)
    _draw_finder(0, 0, set_function)
    _draw_finder(size - 7, 0, set_function)
    _draw_finder(0, size - 7, set_function)
    for i in range(8, size - 8):
        value = i % 2 == 0
        set_function(i, 6, value)
        set_function(6, i, value)
    for y in _ALIGNMENT_POSITIONS[version]:
        for x in _ALIGNMENT_POSITIONS[version]:
            if function_modules[y][x]:
                continue
            _draw_alignment(x, y, set_function)
    set_function(8, 4 * version + 9, True)
    _reserve_format_areas(modules, function_modules)


def _draw_finder(left: int, top: int, set_function) -> None:
    for y in range(-1, 8):
        for x in range(-1, 8):
            xx = left + x
            yy = top + y
            if xx < 0 or yy < 0:
                continue
            try:
                value = 0 <= x <= 6 and 0 <= y <= 6 and (x in {0, 6} or y in {0, 6} or (2 <= x <= 4 and 2 <= y <= 4))
                set_function(xx, yy, value)
            except IndexError:
                continue


def _draw_alignment(center_x: int, center_y: int, set_function) -> None:
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            value = max(abs(dx), abs(dy)) != 1
            set_function(center_x + dx, center_y + dy, value)


def _reserve_format_areas(modules, function_modules) -> None:
    size = len(modules)
    for i in range(9):
        if modules[8][i] is None:
            function_modules[8][i] = True
        if modules[i][8] is None:
            function_modules[i][8] = True
    for i in range(8):
        function_modules[8][size - 1 - i] = True
        function_modules[size - 1 - i][8] = True


def _draw_codewords(modules, function_modules, bits: list[int], mask: int) -> None:
    size = len(modules)
    bit_index = 0
    upward = True
    x = size - 1
    while x > 0:
        if x == 6:
            x -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for y in rows:
            for xx in (x, x - 1):
                if function_modules[y][xx]:
                    continue
                bit = bool(bits[bit_index]) if bit_index < len(bits) else False
                bit_index += 1
                modules[y][xx] = bit ^ _mask_bit(mask, xx, y)
        upward = not upward
        x -= 2


def _mask_bit(mask: int, x: int, y: int) -> bool:
    if mask != 0:
        raise ValueError("Only QR mask 0 is supported")
    return (x + y) % 2 == 0


def _draw_format_bits(modules, function_modules, mask: int) -> None:
    size = len(modules)
    bits = _format_bits(mask)

    def set_format(x: int, y: int, i: int) -> None:
        modules[y][x] = ((bits >> i) & 1) != 0
        function_modules[y][x] = True

    for i in range(6):
        set_format(8, i, i)
    set_format(8, 7, 6)
    set_format(8, 8, 7)
    set_format(7, 8, 8)
    for i in range(9, 15):
        set_format(14 - i, 8, i)
    for i in range(8):
        set_format(size - 1 - i, 8, i)
    for i in range(8, 15):
        set_format(8, size - 15 + i, i)
    modules[size - 8][8] = True


def _format_bits(mask: int) -> int:
    data = (1 << 3) | mask  # Error correction level L.
    value = data << 10
    generator = 0x537
    for i in range(14, 9, -1):
        if (value >> i) & 1:
            value ^= generator << (i - 10)
    return ((data << 10) | value) ^ 0x5412


def _reed_solomon_remainder(data: list[int], degree: int) -> list[int]:
    generator = _rs_generator(degree)
    result = [0] * degree
    for value in data:
        factor = value ^ result.pop(0)
        result.append(0)
        if factor:
            for i, coefficient in enumerate(generator[1:]):
                result[i] ^= _gf_multiply(coefficient, factor)
    return result


def _rs_generator(degree: int) -> list[int]:
    result = [1]
    for i in range(degree):
        result = _poly_multiply(result, [1, _gf_pow(2, i)])
    return result


def _poly_multiply(left: list[int], right: list[int]) -> list[int]:
    result = [0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            result[i + j] ^= _gf_multiply(a, b)
    return result


def _gf_pow(value: int, power: int) -> int:
    result = 1
    for _ in range(power):
        result = _gf_multiply(result, value)
    return result


def _gf_multiply(left: int, right: int) -> int:
    result = 0
    while right:
        if right & 1:
            result ^= left
        right >>= 1
        left <<= 1
        if left & 0x100:
            left ^= 0x11D
    return result

