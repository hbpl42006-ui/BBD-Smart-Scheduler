"""Conservative secondary name checks after a faculty Employee Code lookup."""

import re
import unicodedata


HONORIFICS = {'dr', 'doctor', 'mr', 'mister', 'ms', 'miss', 'mrs', 'prof', 'professor'}


def normalize_person_name(value):
    """Return meaningful, casefolded name tokens without leading titles."""
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    tokens = re.findall(r'[^\W_]+', text, flags=re.UNICODE)
    while tokens and tokens[0] in HONORIFICS:
        tokens.pop(0)
    return tuple(tokens)


def faculty_name_match_kind(uploaded_name, canonical_name):
    """Return EXACT, ABBREVIATED, or MISMATCH for a resolved faculty member."""
    uploaded = normalize_person_name(uploaded_name)
    canonical = normalize_person_name(canonical_name)
    if not uploaded or not canonical:
        return 'MISMATCH'
    if uploaded == canonical:
        return 'EXACT'
    # A complete first name (not an initial) can stand for a longer name.
    if len(uploaded) < len(canonical) and len(uploaded[0]) >= 3 and uploaded[0] == canonical[0]:
        if canonical[:len(uploaded)] == uploaded:
            return 'ABBREVIATED'
        # First and family names may omit one or more middle names. Keep
        # their order and require the canonical family name for this form.
        if len(uploaded) > 1 and uploaded[-1] == canonical[-1]:
            remaining = iter(canonical[1:])
            if all(any(token == candidate for candidate in remaining) for token in uploaded[1:]):
                return 'ABBREVIATED'
    return 'MISMATCH'


def faculty_name_matches(uploaded_name, canonical_name):
    return faculty_name_match_kind(uploaded_name, canonical_name) != 'MISMATCH'
