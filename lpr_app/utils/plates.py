"""
License plate string normalisation for Vietnamese plate layouts.

The same function normalises plates typed into the registry and plates read
by OCR, so matching is an exact comparison of normalised strings. What matters
is that a plate and its common misreadings map to the same key, and that two
different real plates never do.
"""

import re

# Characters the model confuses with digits, coerced at digit positions
TO_DIGIT = {
    'O': '0', 'D': '0', 'Q': '0',
    'I': '1', 'L': '1', 'T': '1',
    'Z': '2',
    'S': '5',
    'G': '6',
    'B': '8',
    'A': '4',
}

# Digits the model confuses with series letters, coerced at the series position
TO_LETTER = {
    '0': 'D',
    '2': 'Z',
    '4': 'A',
    '5': 'S',
    '6': 'G',
    '8': 'B',
}

# Vietnamese plates: 2 province digits, a series (letter, letter+digit, or two
# letters), then 4-5 digits -> 7 to 9 characters once separators are removed.
MIN_LENGTH = 7
MAX_LENGTH = 9

_NON_ALNUM = re.compile(r'[^A-Z0-9]')


def clean_plate(raw):
    """Uppercase and strip everything except ASCII letters and digits."""
    if not raw:
        return ''
    text = str(raw).upper().replace('Đ', 'D')
    return _NON_ALNUM.sub('', text)


def normalize_plate(raw):
    """
    Normalise a plate string for storage and exact matching.

    Positions 0-1 (province) and 4+ (serial) must be digits; position 2 must be
    a letter; position 3 is a digit on most plates and a letter on two-letter
    series, so it is only coerced when the character is a digit-confusable.
    Strings that do not fit the layout are returned cleaned but uncoerced.
    """
    cleaned = clean_plate(raw)
    if not MIN_LENGTH <= len(cleaned) <= MAX_LENGTH:
        return cleaned

    chars = [
        TO_LETTER.get(ch, ch) if i == 2 else TO_DIGIT.get(ch, ch)
        for i, ch in enumerate(cleaned)
    ]

    coerced = ''.join(chars)
    if not _fits_layout(coerced):
        return cleaned
    return coerced


def _fits_layout(plate):
    if not (plate[0].isdigit() and plate[1].isdigit()):
        return False
    if not plate[2].isalpha():
        return False
    # 7-character plates have a single-letter series, so position 3 is a digit
    if len(plate) == MIN_LENGTH and not plate[3].isdigit():
        return False
    return plate[4:].isdigit()


def within_one_edit(a, b):
    """True if a and b differ by exactly one substitution, insertion or deletion."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if la > lb:
        a, b = b, a
    # b is one longer than a: skipping one char of b must yield a
    for i in range(len(b)):
        if b[:i] + b[i + 1:] == a:
            return True
    return False
