# Blind solving (proof that a task is solvable from its prompt alone)

You are proving that v2 challenges are solvable exactly as a model sees them. Work from
the repository root.

**You must not read anything under `langbench/v2/challenges/<name>/` except to write your solution files** —
not `challenge.py`, not `fixtures/`, not `ref/main.py` or other references, not the generated data under the
temp directories. Do not read `langbench/v2/DESIGN.md` either. Your only sources are:

- `python3 bench.py prompt <name> <lang>` — the exact prompt a model receives.
- `python3 bench.py try <name> <lang> <file>` — grades your file on the example and hidden cases and
  prints exactly the feedback a model would get. Exit status 0 = PASS.
- Your own knowledge of the language and of the formats/protocols involved, and small local experiments in a
  scratch directory (e.g. inspecting your own test inputs). You may write your own test inputs.

Write each solution to `langbench/v2/challenges/<name>/ref/<file>` (`Main.java`, `main.swift`, `main.ts`, `main.c`,
`main.rs`). Write it as a model would — one self-contained file, standard library only (C: libc plus macOS
system headers that need no extra link flags; Swift: Foundation and Apple frameworks; Rust: std only;
TypeScript: Node built-ins only).

Count every `try` run. When a language passes, append one line to `langbench/v2/challenges/<name>/ref/ATTEMPTS.tsv`:
`<lang>\t<number of try runs until PASS>\t<one-line note on what failed before the pass, or "first try">`.

**If you believe the prompt is ambiguous, contradictory or wrong** (the feedback says your output is wrong but
the prompt supports your reading), do not guess endlessly: after 3 such failures, stop that language, and
report the exact sentence of the prompt and the two readings. That report is as valuable as a pass. Do not
weaken your solution to match a suspected bug in the expected output.

Final report: per language the try count, and any spec problems found (quote the prompt text).
