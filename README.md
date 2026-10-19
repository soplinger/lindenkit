# lindenkit

**An offline L-system toolkit: write a plant or fractal as a short grammar file, then lint it, expand it, measure the turtle's path, and draw it.**

lindenkit reads plain-text L-system grammars (the rewriting systems behind plant growth and fractal curves) and turns them into exact strings, growth tables, turtle statistics, SVG pictures and ASCII previews. Every grammar problem is reported with its file and line number, and `check` fails CI when a grammar grows too big. It is one Python file, uses only the standard library, and makes no network calls.

## Features

- **Line-numbered lint.** Catches unmatched or unclosed `[` `]`, invalid symbols, missing `axiom` or `angle`, unknown settings, bad weights, duplicate rules, and rules that can never apply.
- **Deterministic expansion** of any grammar, with `-n` to override the iteration count stored in the file.
- **Seeded stochastic rules.** Give rules weights, e.g. `F (3) -> ...`, and each `--seed` gives a reproducible tree.
- **Runaway protection.** `--max-length` stops an expansion before it exhausts memory, and reports the iteration where it happened.
- **Growth table** for every generation: symbol count, drawn-segment count and growth ratio.
- **Turtle statistics:** drawn segments, total path length, width, height, branch count and maximum branch depth.
- **SVG export** with a single `<path>`, scaled to fit a square canvas, with the grammar name as an escaped `<title>`. Writes to a file or stdout.
- **ASCII preview** rasterised to a set width, with rows adjusted for terminal character aspect.
- **CI gate** (`check`) with optional limits on symbols, segments, width and height. Exits 1 when a limit is breached.
- **Six built-in presets** (`koch`, `dragon`, `hilbert`, `sierpinski`, `plant`, `bush`), used as `@name` anywhere a file is expected.
- **Documented exit codes:** `0` success, `1` lint errors or a failed gate, `2` a bad command line or unreadable file.
- **Standard library only.** No accounts, API keys, servers or network access.

## Quick start

```sh
python3 lindenkit.py lint examples/koch.lsys
python3 lindenkit.py table @koch
python3 lindenkit.py ascii @koch -n 3 --width 50
python3 lindenkit.py svg examples/plant.lsys -o plant.svg
python3 lindenkit.py check examples/bush.lsys --seed 7 --max-segments 1000
```

A grammar file looks like this (`examples/koch.lsys`):

```
name: Koch snowflake edge
axiom: F
angle: 60
iterations: 4
F -> F+F--F+F
```

`F` and `G` draw a unit step, `f` and `g` move without drawing, `+` and `-` turn by `angle`, and `[` `]` save and restore the turtle's position and heading. Any other letter is a variable or constant: it is rewritten if it has a rule and left alone otherwise.

`table @koch` prints one row per generation up to the preset's 4 iterations. The first three rows, worked out by hand from the rules (not captured from a run), look like this:

```
iter    symbols      drawn   growth
   0          1          1        -
   1          8          4    8.000
   2         36         16    4.500
```

Running `lint` on the deliberately broken example shows the kind of output to expect:

```
$ python3 lindenkit.py lint examples/broken.lsys
examples/broken.lsys:3: error: '[' never closed in axiom
examples/broken.lsys:6: error: ']' without matching '[' in rule for 'F'
examples/broken.lsys:7: warning: rule for 'G' never applies; 'G' is not reachable from the axiom
examples/broken.lsys:8: error: weight must be a positive number
examples/broken.lsys:9: error: unknown setting 'flip'
examples/broken.lsys: 4 error(s), 1 warning(s)
```

## How to run

Requires Python 3.10 or newer.

```sh
python3 lindenkit.py presets                 # list the built-in grammars
python3 lindenkit.py presets dragon          # print one preset's grammar
python3 lindenkit.py lint FILE|@PRESET
python3 lindenkit.py expand FILE|@PRESET [-n N] [--seed S] [--max-length M]
python3 lindenkit.py stats FILE|@PRESET [-n N] [--seed S]
python3 lindenkit.py table FILE|@PRESET [-n N] [--seed S]
python3 lindenkit.py svg FILE|@PRESET [-n N] [--seed S] [--size PX] [-o OUT.svg]
python3 lindenkit.py ascii FILE|@PRESET [-n N] [--seed S] [--width COLS]
python3 lindenkit.py check FILE|@PRESET [--max-symbols N] [--max-segments N] \
                                        [--max-width N] [--max-height N]
```

`check` lints first, prints every issue, and skips the expansion if there are errors. It then applies the size gates in step-length units.

## How to test

The tests use `pytest`, so install it first (`python3 -m pip install pytest`). Then run:

```sh
python3 -m pytest -q tests
```

The tests cover parsing, line-numbered lint, expansion, weighted and seeded stochastic rules, the length cap, turtle geometry, growth tables, SVG well-formedness, ASCII fit, CI gates and exit codes, and that every built-in preset lints clean.

## Project layout

```
lindenkit.py          the whole tool: parser, lint, expansion, turtle, exports, CLI
examples/             sample grammars (koch, plant, bush) and a deliberately broken one
tests/conftest.py     puts the project root on sys.path
tests/test_lindenkit.py
```
