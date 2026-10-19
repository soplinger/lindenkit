import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import lindenkit as lk

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


def _warnings(issues):
    return [i for i in issues if i.severity == "warning"]


def test_parse_reads_headers_rules_and_weights():
    text = (
        "name: Demo\n"
        "axiom: F\n"
        "angle: 90\n"
        "iterations: 2\n"
        "F (3) -> F+F\n"
        "F -> F-F\n"
    )
    g = lk.parse_grammar(text)
    assert g.name == "Demo"
    assert g.axiom == "F"
    assert g.angle == 90.0
    assert g.iterations == 2
    assert [(r.symbol, r.weight, r.body, r.line) for r in g.rules] == [
        ("F", 3.0, "F+F", 5),
        ("F", 1.0, "F-F", 6),
    ]


def test_comments_and_blank_lines_are_ignored():
    g = lk.parse_grammar("# note\n\naxiom: F # trailing\nangle: 60\nF -> F+F # rule\n")
    assert g.axiom == "F"
    assert lk.lint(g) == []


def test_lint_reports_unmatched_close_bracket_with_line_number():
    g = lk.parse_grammar("axiom: F\nangle: 25\nF -> F]F\n")
    errors = _errors(lk.lint(g))
    assert len(errors) == 1
    assert errors[0].line == 3
    assert "without matching" in errors[0].message


def test_lint_reports_unclosed_bracket_on_axiom_line():
    g = lk.parse_grammar("axiom: F[+F\nangle: 25\n")
    errors = _errors(lk.lint(g))
    assert [(e.line, "never closed" in e.message) for e in errors] == [(1, True)]


def test_lint_rejects_invalid_symbols():
    g = lk.parse_grammar("axiom: F\nangle: 60\nF -> F*F\n")
    errors = _errors(lk.lint(g))
    assert len(errors) == 1
    assert errors[0].line == 3
    assert "'*'" in errors[0].message


def test_lint_requires_axiom_and_angle():
    issues = _errors(lk.lint(lk.parse_grammar("F -> F+F\n")))
    messages = sorted(i.message for i in issues)
    assert messages == ["missing 'angle'", "missing 'axiom'"]


def test_lint_flags_unknown_setting_and_bad_weight_with_lines():
    g = lk.parse_grammar("axiom: F\nangle: 60\nflip: yes\nF (0) -> F\n")
    lines = {i.line for i in _errors(lk.lint(g))}
    assert lines == {3, 4}


def test_lint_warns_about_unreachable_rule():
    g = lk.parse_grammar("axiom: F\nangle: 60\nF -> F+F\nG -> G-G\n")
    warnings = _warnings(lk.lint(g))
    assert [(w.line, "never applies" in w.message) for w in warnings] == [(4, True)]


def test_koch_expansion_matches_known_strings():
    g = lk.parse_grammar("axiom: F\nangle: 60\nF -> F+F--F+F\n")
    assert lk.expand(g, 1) == "F+F--F+F"
    assert lk.expand(g, 2).count("F") == 16
    assert len(lk.expand(g, 2)) == 36


def test_growth_table_lengths_drawn_counts_and_ratios():
    g = lk.parse_grammar("axiom: F\nangle: 60\nF -> F+F--F+F\n")
    rows = lk.growth_rows(g, 2)
    assert [r.length for r in rows] == [1, 8, 36]
    assert [r.drawn for r in rows] == [1, 4, 16]
    assert rows[0].ratio is None
    assert rows[1].ratio == pytest.approx(8.0)


def test_expansion_length_cap_raises():
    g = lk.parse_grammar("axiom: F\nangle: 60\nF -> F+F--F+F\n")
    with pytest.raises(lk.ExpansionTooLong):
        lk.expand(g, 3, max_length=10)


def test_invalid_grammar_is_refused_by_expand():
    g = lk.parse_grammar("axiom: F[\nangle: 60\n")
    with pytest.raises(lk.GrammarError, match="invalid grammar"):
        lk.expand(g)


def test_seeded_stochastic_expansion_is_reproducible():
    g = lk.parse_grammar(
        "axiom: F\nangle: 25\nF (1) -> F[+F]F\nF (1) -> F[-F]F\nF (1) -> F[+F]F[-F]F\n"
    )
    first = lk.expand(g, 4, seed=7)
    assert lk.expand(g, 4, seed=7) == first
    assert len({lk.expand(g, 4, seed=s) for s in range(1, 6)}) > 1


def test_weighted_rules_follow_their_weights():
    g = lk.parse_grammar("axiom: " + "F" * 500 + "\nangle: 0\nF (1000) -> a\nF (1) -> b\n")
    result = lk.expand(g, 1, seed=3)
    assert result.count("a") > 450
    assert result.count("b") < 10


def test_square_path_closes_back_on_origin():
    segments = lk.trace("F+F+F+F", 90)
    assert len(segments) == 4
    end = segments[-1][1]
    assert end[0] == pytest.approx(0.0, abs=1e-9)
    assert end[1] == pytest.approx(0.0, abs=1e-9)
    stats = lk.measure("F+F+F+F", segments)
    assert stats.width == pytest.approx(1.0)
    assert stats.height == pytest.approx(1.0)
    assert stats.length == pytest.approx(4.0)


def test_brackets_restore_position_and_heading():
    text = "F[+F]F"
    segments = lk.trace(text, 90)
    assert len(segments) == 3
    stats = lk.measure(text, segments)
    assert stats.width == pytest.approx(1.0)
    assert stats.height == pytest.approx(2.0)
    assert stats.branches == 1
    assert stats.max_depth == 1


def test_svg_is_well_formed_and_has_one_move_per_segment():
    g = lk.parse_grammar(
        (EXAMPLES / "koch.lsys").read_text(encoding="utf-8")
    )
    text = lk.expand(g, 2)
    svg = lk.to_svg(lk.trace(text, g.angle), title=g.name)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    path = [el for el in root.iter() if el.tag.endswith("path")][0]
    assert path.get("d").count("M") == len(lk.trace(text, g.angle))


def test_ascii_picture_fits_requested_width():
    g = lk.parse_grammar("axiom: F\nangle: 60\nF -> F+F--F+F\n")
    picture = lk.to_ascii(lk.trace(lk.expand(g, 3), 60), width=40)
    lines = picture.splitlines()
    assert "#" in picture
    assert all(len(line) <= 40 for line in lines)


def test_check_fails_when_segment_gate_is_exceeded(tmp_path, capsys):
    f = tmp_path / "koch.lsys"
    f.write_text("axiom: F\nangle: 60\nF -> F+F--F+F\n", encoding="utf-8")
    code = lk.main(["check", str(f), "-n", "3", "--max-segments", "10"])
    assert code == 1
    assert "exceeds limit" in capsys.readouterr().out


def test_check_passes_preset_within_gates(capsys):
    assert lk.main(["check", "@koch", "-n", "3", "--max-segments", "1000"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_lint_exit_codes_for_examples_and_broken_file():
    assert lk.main(["lint", str(EXAMPLES / "koch.lsys")]) == 0
    assert lk.main(["lint", str(EXAMPLES / "plant.lsys")]) == 0
    assert lk.main(["lint", str(EXAMPLES / "bush.lsys")]) == 0
    assert lk.main(["lint", str(EXAMPLES / "broken.lsys")]) == 1


def test_missing_file_gives_usage_exit_code(capsys):
    assert lk.main(["lint", "no-such-file.lsys"]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_every_preset_lints_clean():
    for name, text in lk.PRESETS.items():
        issues = lk.lint(lk.parse_grammar(text, name))
        assert _errors(issues) == [], name


def test_svg_command_writes_output_file(tmp_path):
    out = tmp_path / "hilbert.svg"
    assert lk.main(["svg", "@hilbert", "-n", "2", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("<svg")
