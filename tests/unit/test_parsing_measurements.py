from __future__ import annotations

import pytest

from blueprint_pipeline.evidence.identity import normalize_label
from blueprint_pipeline.evidence.measurements import parse_length, values_agree
from blueprint_pipeline.inference.parsing import parse_json_object


@pytest.mark.parametrize(
    "raw,mm,unit",
    [
        ("3200 mm", 3200.0, "mm"),
        ("3.2 m", 3200.0, "m"),
        ("320cm", 3200.0, "cm"),
        ("10' 6\"", 3200.4, "ft-in"),
        ("10ft", 3048.0, "ft-in"),
        ("12 in", 304.8, "in"),
    ],
)
def test_parse_length(raw, mm, unit):
    assert parse_length(raw)[:2] == (mm, unit)


@pytest.mark.parametrize(
    "raw,why",
    [
        ("3200", "without unit"),
        ("3,20 m", "comma is ambiguous"),
        ("3 x 4 m", "compound"),
        ("10' 13\"", "inches component"),
        ("", "no text"),
        (None, "no text"),
        ("approx 3m-ish", "unrecognised"),
    ],
)
def test_parse_length_never_guesses(raw, why):
    value, _unit, unc = parse_length(raw)
    assert value is None and why in unc


def test_values_agree_tolerance():
    assert values_agree(3000, 3040)
    assert not values_agree(2500, 2800)
    assert values_agree(20000, 20350)  # 2 % relative


def test_label_normalization_keeps_null():
    assert normalize_label(" bravo  rm. ") == "BRAVO RM"
    assert normalize_label("...") is None and normalize_label(None) is None


@pytest.mark.parametrize(
    "text,ok,note",
    [
        ('{"a": 1}', True, None),
        ('```json\n{"a": 1}\n```', True, "code fence"),
        ('<think>x</think>{"a": 1}', True, "<think>"),
        ('Here you go: {"a": 1}', False, None),
        ("[1, 2]", False, None),
        ("", False, None),
        ('{"a": ', False, None),
    ],
)
def test_parse_json_object(text, ok, note):
    obj, err, notes = parse_json_object(text)
    assert (obj is not None) is ok and (err is None) is ok
    if note:
        assert any(note in n for n in notes)
