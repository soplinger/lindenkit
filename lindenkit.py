#!/usr/bin/env python3
"""lindenkit: an offline L-system toolkit.

Reads a plain-text grammar, expands it (deterministic or seeded stochastic),
walks the turtle over the result, and writes SVG or ASCII pictures. Growth
tables, turtle statistics and CI size gates come along for the ride.

Standard library only. No network access.
"""

import argparse
import math
import random
import re
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

# Symbols with a meaning to the turtle. Other letters are variables or constants.
DRAW_SYMBOLS = frozenset("FG")
MOVE_SYMBOLS = frozenset("fg")
ALLOWED = frozenset("+-[]" + string.ascii_letters)

MAX_ITERATIONS = 30
DEFAULT_MAX_LENGTH = 1_000_000
HEADER_KEYS = ("name", "axiom", "angle", "iterations")

RULE_RE = re.compile(r"^([A-Za-z])\s*(?:\(([^)]*)\))?\s*->\s*(.*)$")


class GrammarError(Exception):
    """A grammar that is invalid, or cannot be expanded or traced."""


class ExpansionTooLong(GrammarError):
    """An expansion grew past the --max-length cap."""


class CliError(Exception):
    """A bad command line or an unreadable input file."""


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" or "warning"
    line: int  # 0 when the problem is not tied to one line
    message: str


@dataclass
class Rule:
    symbol: str
    weight: float
    body: str
    line: int


@dataclass
class Grammar:
    name: str = "grammar"
    axiom: str = ""
    angle: float | None = None
    iterations: int = 3
    rules: list[Rule] = field(default_factory=list)
    header_lines: dict[str, int] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)


@dataclass(frozen=True)
class GrowthRow:
    iteration: int
    length: int
    drawn: int
    ratio: float | None


@dataclass(frozen=True)
class TurtleStats:
    segments: int
    length: float
    width: float
    height: float
    branches: int
    max_depth: int


PRESETS = {
    "koch": """\
name: Koch curve
axiom: F
angle: 60
iterations: 4
F -> F+F--F+F
""",
    "dragon": """\
name: Dragon curve
axiom: FX
angle: 90
iterations: 10
X -> X+YF+
Y -> -FX-Y
""",
    "hilbert": """\
name: Hilbert curve
axiom: A
angle: 90
iterations: 4
A -> +BF-AFA-FB+
B -> -AF+BFB+FA-
""",
    "sierpinski": """\
name: Sierpinski arrowhead
axiom: F
angle: 60
iterations: 6
F -> G-F-G
G -> F+G+F
""",
    "plant": """\
name: Branching plant
axiom: X
angle: 25
iterations: 5
X -> F+[[X]-X]-F[-FX]+X
F -> FF
""",
    "bush": """\
name: Stochastic bush
axiom: F
angle: 25.7
iterations: 4
F (1) -> F[+F]F[-F]F
F (1) -> F[+F]F
F (1) -> F[-F]F
""",
}


def _squash(text):
    return re.sub(r"\s+", "", text)


def _where(issue):
    return f"line {issue.line}: {issue.message}" if issue.line else issue.message


def format_issue(label, issue):
    """Render an issue as file:line: severity: message, or file: ... for line 0."""
    if issue.line:
        return f"{label}:{issue.line}: {issue.severity}: {issue.message}"
    return f"{label}: {issue.severity}: {issue.message}"


def parse_grammar(text, name="grammar"):
    """Parse grammar text. Syntax problems are kept as issues, not raised."""
    grammar = Grammar(name=name)
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "->" in line:
            _parse_rule(grammar, line, lineno)
        elif ":" in line:
            _parse_header(grammar, line, lineno)
        else:
            grammar.issues.append(
                Issue("error", lineno, "expected 'key: value' or a rule like 'F -> F+F'")
            )
    return grammar


def _parse_header(grammar, line, lineno):
    key, value = (part.strip() for part in line.split(":", 1))
    key = key.lower()
    if key not in HEADER_KEYS:
        grammar.issues.append(Issue("error", lineno, f"unknown setting '{key}'"))
        return
    if key in grammar.header_lines:
        first = grammar.header_lines[key]
        grammar.issues.append(Issue("error", lineno, f"'{key}' already set on line {first}"))
        return
    grammar.header_lines[key] = lineno
    if key == "name":
        grammar.name = value or grammar.name
    elif key == "axiom":
        grammar.axiom = _squash(value)
    elif key == "angle":
        try:
            grammar.angle = float(value)
        except ValueError:
            grammar.issues.append(
                Issue("error", lineno, f"angle must be a number of degrees, got '{value}'")
            )
    else:
        try:
            count = int(value)
        except ValueError:
            grammar.issues.append(
                Issue("error", lineno, f"iterations must be a whole number, got '{value}'")
            )
            return
        if 0 <= count <= MAX_ITERATIONS:
            grammar.iterations = count
        else:
            grammar.issues.append(
                Issue("error", lineno, f"iterations must be 0-{MAX_ITERATIONS}, got {count}")
            )


def _parse_rule(grammar, line, lineno):
    match = RULE_RE.match(line)
    if not match:
        grammar.issues.append(
            Issue("error", lineno, "malformed rule; expected 'X -> body' or 'X (weight) -> body'")
        )
        return
    symbol, weight_text, body = match.groups()
    weight = 1.0
    if weight_text is not None and weight_text.strip():
        try:
            weight = float(weight_text)
        except ValueError:
            grammar.issues.append(
                Issue("error", lineno, f"weight '{weight_text.strip()}' is not a number")
            )
            return
        if not (math.isfinite(weight) and weight > 0):
            grammar.issues.append(Issue("error", lineno, "weight must be a positive number"))
            return
    grammar.rules.append(Rule(symbol, weight, _squash(body), lineno))


def _check_body(issues, body, line, label):
    for ch in sorted({c for c in body if c not in ALLOWED}):
        issues.append(Issue("error", line, f"invalid symbol {ch!r} in {label}"))
    depth = 0
    for ch in body:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth < 0:
                issues.append(Issue("error", line, f"']' without matching '[' in {label}"))
                return
    if depth > 0:
        issues.append(Issue("error", line, f"'[' never closed in {label}"))


def reachable_symbols(grammar):
    """Letters that can appear in some generation, starting from the axiom."""
    by_symbol = {}
    for rule in grammar.rules:
        by_symbol.setdefault(rule.symbol, []).append(rule)
    seen = set(grammar.axiom)
    frontier = list(seen)
    while frontier:
        symbol = frontier.pop()
        for rule in by_symbol.get(symbol, []):
            for ch in rule.body:
                if ch.isalpha() and ch not in seen:
                    seen.add(ch)
                    frontier.append(ch)
    return seen


def lint(grammar):
    """Return every problem found in a grammar, errors and warnings alike."""
    issues = list(grammar.issues)
    if "axiom" not in grammar.header_lines:
        issues.append(Issue("error", 0, "missing 'axiom'"))
    else:
        _check_body(issues, grammar.axiom, grammar.header_lines["axiom"], "axiom")
    if "angle" not in grammar.header_lines:
        issues.append(Issue("error", 0, "missing 'angle'"))
    elif grammar.angle is not None and not math.isfinite(grammar.angle):
        issues.append(Issue("error", grammar.header_lines["angle"], "angle must be finite"))

    first_seen = {}
    for rule in grammar.rules:
        _check_body(issues, rule.body, rule.line, f"rule for '{rule.symbol}'")
        key = (rule.symbol, rule.body)
        if key in first_seen:
            issues.append(
                Issue("warning", rule.line,
                      f"duplicate of the rule on line {first_seen[key]}")
            )
        else:
            first_seen[key] = rule.line

    if grammar.axiom and "axiom" in grammar.header_lines:
        reached = reachable_symbols(grammar)
        for rule in grammar.rules:
            if rule.symbol not in reached:
                issues.append(
                    Issue("warning", rule.line,
                          f"rule for '{rule.symbol}' never applies; '{rule.symbol}' "
                          "is not reachable from the axiom")
                )
        if grammar.iterations > 0 and not any(r.symbol in reached for r in grammar.rules):
            issues.append(
                Issue("warning", 0, "no rule applies to the axiom; the expansion never changes")
            )
    return sorted(issues, key=lambda i: i.line)


def has_errors(issues):
    return any(issue.severity == "error" for issue in issues)


def _require_valid(grammar):
    errors = [issue for issue in lint(grammar) if issue.severity == "error"]
    if errors:
        raise GrammarError(f"invalid grammar: {_where(errors[0])}")


def _pick(rules, symbol, rng):
    """Choose the body for one symbol. Symbols without rules are kept as they are."""
    if not rules:
        return symbol
    if rng is None or len(rules) == 1:
        return rules[0].body
    chosen = rng.choices(rules, weights=[rule.weight for rule in rules])[0]
    return chosen.body


def generations(grammar, iterations=None, seed=None, max_length=DEFAULT_MAX_LENGTH):
    """Return an iterator over the axiom and each rewritten generation.

    Without a seed the first rule for each symbol is used. With a seed, rules
    that share a symbol are chosen by weight, reproducibly.
    """
    _require_valid(grammar)
    count = grammar.iterations if iterations is None else iterations
    if not 0 <= count <= MAX_ITERATIONS:
        raise GrammarError(f"iterations must be 0-{MAX_ITERATIONS}, got {count}")
    if max_length < 1:
        raise GrammarError("max_length must be at least 1")

    table = {}
    for rule in grammar.rules:
        table.setdefault(rule.symbol, []).append(rule)
    rng = random.Random(seed) if seed is not None else None

    def _iterate():
        current = grammar.axiom
        yield current
        for step in range(1, count + 1):
            parts = []
            size = 0
            for symbol in current:
                body = _pick(table.get(symbol), symbol, rng)
                size += len(body)
                if size > max_length:
                    raise ExpansionTooLong(
                        f"expansion exceeds --max-length {max_length} at iteration {step}"
                    )
                parts.append(body)
            current = "".join(parts)
            yield current

    return _iterate()


def expand(grammar, iterations=None, seed=None, max_length=DEFAULT_MAX_LENGTH):
    """Return the string after the given number of rewriting steps."""
    last = ""
    for last in generations(grammar, iterations, seed, max_length):
        pass
    return last


def growth_rows(grammar, iterations=None, seed=None, max_length=DEFAULT_MAX_LENGTH):
    """Length, drawn-segment count and growth ratio for every generation."""
    rows = []
    previous = None
    for index, text in enumerate(generations(grammar, iterations, seed, max_length)):
        length = len(text)
        ratio = length / previous if previous else None
        drawn = sum(ch in DRAW_SYMBOLS for ch in text)
        rows.append(GrowthRow(index, length, drawn, ratio))
        previous = length
    return rows


def trace(text, angle):
    """Walk the turtle over `text` and return the drawn segments.

    The turtle starts at the origin facing up. F draws a unit step, f moves
    without drawing, + and - turn by the angle, and [ ] save and restore state.
    """
    x = y = 0.0
    heading = 90.0
    stack = []
    segments = []
    for ch in text:
        if ch in DRAW_SYMBOLS or ch in MOVE_SYMBOLS:
            nx = x + math.cos(math.radians(heading))
            ny = y + math.sin(math.radians(heading))
            if ch in DRAW_SYMBOLS:
                segments.append(((x, y), (nx, ny)))
            x, y = nx, ny
        elif ch == "+":
            heading += angle
        elif ch == "-":
            heading -= angle
        elif ch == "[":
            stack.append((x, y, heading))
        elif ch == "]":
            if not stack:
                raise GrammarError("']' without matching '['")
            x, y, heading = stack.pop()
    if stack:
        raise GrammarError("'[' never closed")
    return segments


def _bounds(segments):
    points = [point for segment in segments for point in segment]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def measure(text, segments):
    """Summarise a traced expansion: counts, path length, extent and branching."""
    depth = max_depth = 0
    for ch in text:
        if ch == "[":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == "]":
            depth -= 1
    if segments:
        min_x, min_y, max_x, max_y = _bounds(segments)
        width, height = max_x - min_x, max_y - min_y
        length = sum(math.dist(a, b) for a, b in segments)
    else:
        width = height = length = 0.0
    return TurtleStats(len(segments), length, width, height, text.count("["), max_depth)


def to_svg(segments, title="lindenkit", size=600, margin=10):
    """Render segments as one SVG path, scaled to fit a square canvas."""
    if segments:
        min_x, min_y, max_x, max_y = _bounds(segments)
    else:
        min_x = min_y = max_x = max_y = 0.0
    span = max(max_x - min_x, max_y - min_y) or 1.0
    scale = (size - 2 * margin) / span
    width = (max_x - min_x) * scale + 2 * margin
    height = (max_y - min_y) * scale + 2 * margin

    def px(x):
        return (x - min_x) * scale + margin

    def py(y):
        return (max_y - y) * scale + margin

    d = "".join(
        f"M{px(a[0]):.2f} {py(a[1]):.2f}L{px(b[0]):.2f} {py(b[1]):.2f}"
        for a, b in segments
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.2f}" height="{height:.2f}" '
        f'viewBox="0 0 {width:.2f} {height:.2f}">\n'
        f"  <title>{escape(title)}</title>\n"
        f'  <path d="{d}" fill="none" stroke="#2f5d3a" stroke-width="1" '
        'stroke-linecap="round"/>\n'
        "</svg>\n"
    )


def to_ascii(segments, width=60):
    """Rasterise segments onto a character grid. Rows are half as tall as columns are wide."""
    if not segments:
        return ""
    min_x, min_y, max_x, max_y = _bounds(segments)
    span = max(max_x - min_x, max_y - min_y) or 1.0
    scale = (width - 1) / span
    row_scale = scale / 2
    rows = int(round((max_y - min_y) * row_scale)) + 1
    grid = [[" "] * width for _ in range(rows)]
    for (x1, y1), (x2, y2) in segments:
        steps = max(1, int(math.dist((x1, y1), (x2, y2)) * scale * 2) + 1)
        for k in range(steps + 1):
            t = k / steps
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            col = min(max(int(round((x - min_x) * scale)), 0), width - 1)
            row = min(max(int(round((max_y - y) * row_scale)), 0), rows - 1)
            grid[row][col] = "#"
    return "\n".join("".join(line).rstrip() for line in grid)


def load(source):
    """Load a grammar from a file path, or from a built-in preset named @NAME."""
    if source.startswith("@"):
        name = source[1:]
        if name not in PRESETS:
            raise CliError(f"unknown preset '{name}'; run 'lindenkit presets' to list them")
        return parse_grammar(PRESETS[name], name)
    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"cannot read {source}: {exc.strerror}") from exc
    return parse_grammar(text, path.stem)


def _fmt(value):
    return f"{value:g}"


def cmd_lint(args):
    grammar = load(args.grammar)
    issues = lint(grammar)
    for issue in issues:
        print(format_issue(args.grammar, issue))
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = len(issues) - errors
    if errors:
        print(f"{args.grammar}: {errors} error(s), {warnings} warning(s)")
        return 1
    print(f"{args.grammar}: ok ({len(grammar.rules)} rules, {warnings} warning(s))")
    return 0


def cmd_expand(args):
    grammar = load(args.grammar)
    print(expand(grammar, args.iterations, args.seed, args.max_length))
    return 0


def cmd_stats(args):
    grammar = load(args.grammar)
    text = expand(grammar, args.iterations, args.seed, args.max_length)
    stats = measure(text, trace(text, grammar.angle))
    print(f"name:        {grammar.name}")
    print(f"angle:       {_fmt(grammar.angle)}")
    print(f"symbols:     {len(text)}")
    print(f"segments:    {stats.segments}")
    print(f"path length: {stats.length:.3f}")
    print(f"width:       {stats.width:.3f}")
    print(f"height:      {stats.height:.3f}")
    print(f"branches:    {stats.branches}")
    print(f"max depth:   {stats.max_depth}")
    return 0


def cmd_table(args):
    grammar = load(args.grammar)
    print(f"{'iter':>4} {'symbols':>10} {'drawn':>10} {'growth':>8}")
    for row in growth_rows(grammar, args.iterations, args.seed, args.max_length):
        ratio = "-" if row.ratio is None else f"{row.ratio:.3f}"
        print(f"{row.iteration:>4} {row.length:>10} {row.drawn:>10} {ratio:>8}")
    return 0


def cmd_svg(args):
    grammar = load(args.grammar)
    text = expand(grammar, args.iterations, args.seed, args.max_length)
    svg = to_svg(trace(text, grammar.angle), title=grammar.name, size=args.size)
    if args.output:
        Path(args.output).write_text(svg, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(svg, end="")
    return 0


def cmd_ascii(args):
    grammar = load(args.grammar)
    text = expand(grammar, args.iterations, args.seed, args.max_length)
    picture = to_ascii(trace(text, grammar.angle), width=args.width)
    print(picture if picture else "(nothing drawn)")
    return 0


def cmd_check(args):
    label = args.grammar
    grammar = load(label)
    issues = lint(grammar)
    for issue in issues:
        print(format_issue(label, issue))
    if has_errors(issues):
        print(f"{label}: FAIL lint")
        return 1
    text = expand(grammar, args.iterations, args.seed, args.max_length)
    stats = measure(text, trace(text, grammar.angle))
    gates = [
        ("symbols", len(text), args.max_symbols),
        ("segments", stats.segments, args.max_segments),
        ("width", stats.width, args.max_width),
        ("height", stats.height, args.max_height),
    ]
    failures = [
        f"{name} {_fmt(value)} exceeds limit {_fmt(limit)}"
        for name, value, limit in gates
        if limit is not None and value > limit
    ]
    for failure in failures:
        print(f"{label}: FAIL {failure}")
    if failures:
        return 1
    print(f"{label}: PASS symbols={len(text)} segments={stats.segments} "
          f"width={stats.width:.3f} height={stats.height:.3f}")
    return 0


def cmd_presets(args):
    if args.name is None:
        for name, text in PRESETS.items():
            grammar = parse_grammar(text, name)
            print(f"{name:<12} {grammar.name} (angle {_fmt(grammar.angle)}, "
                  f"{grammar.iterations} iterations)")
        return 0
    if args.name not in PRESETS:
        raise CliError(f"unknown preset '{args.name}'")
    print(PRESETS[args.name], end="")
    return 0


def _add_generation_options(parser):
    parser.add_argument("-n", "--iterations", type=int, default=None,
                        help="rewriting steps (default: the grammar's iterations)")
    parser.add_argument("--seed", type=int, default=None,
                        help="seed for weighted stochastic rules")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH,
                        help="stop if an expansion passes this many symbols")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lindenkit",
        description="Offline L-system toolkit: lint, expand, measure and draw plant-style grammars.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lint", help="check a grammar file for errors and warnings")
    p.add_argument("grammar", help="grammar file or @preset")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("expand", help="print the expanded string")
    p.add_argument("grammar")
    _add_generation_options(p)
    p.set_defaults(func=cmd_expand)

    p = sub.add_parser("stats", help="turtle statistics for the expanded string")
    p.add_argument("grammar")
    _add_generation_options(p)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("table", help="growth table for every generation")
    p.add_argument("grammar")
    _add_generation_options(p)
    p.set_defaults(func=cmd_table)

    p = sub.add_parser("svg", help="write an SVG picture")
    p.add_argument("grammar")
    p.add_argument("-o", "--output", help="output file (default: stdout)")
    p.add_argument("--size", type=int, default=600, help="canvas size in pixels")
    _add_generation_options(p)
    p.set_defaults(func=cmd_svg)

    p = sub.add_parser("ascii", help="print a text picture of the drawing")
    p.add_argument("grammar")
    p.add_argument("--width", type=int, default=60, help="picture width in characters")
    _add_generation_options(p)
    p.set_defaults(func=cmd_ascii)

    p = sub.add_parser("check", help="lint plus CI size gates; exit 1 on any failure")
    p.add_argument("grammar")
    p.add_argument("--max-symbols", type=float, default=None)
    p.add_argument("--max-segments", type=float, default=None)
    p.add_argument("--max-width", type=float, default=None, help="in step lengths")
    p.add_argument("--max-height", type=float, default=None, help="in step lengths")
    _add_generation_options(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("presets", help="list built-in grammars, or print one")
    p.add_argument("name", nargs="?", help="preset to print")
    p.set_defaults(func=cmd_presets)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"lindenkit: {exc}", file=sys.stderr)
        return 2
    except GrammarError as exc:
        print(f"lindenkit: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
