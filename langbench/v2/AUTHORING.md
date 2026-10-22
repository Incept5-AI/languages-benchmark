# Authoring a v2 challenge

Read `harness.py` first (it is short). Do **not** edit `harness.py` or `bench.py`; if you need a harness change,
say so in your report.

## Files

```
challenges/<name>/challenge.py   # class Challenge(H.Challenge): prompt(), case(seed, ctx), optional start()/stop()
challenges/<name>/ref/main.py    # Python reference solution, written FROM THE PROMPT ONLY
challenges/<name>/fixtures/      # optional static inputs (e.g. copied binary files); keep < 2 MB total
```

`challenges/jwt_verify/` is the worked example — copy its structure.

## Rules

1. **Determinism.** `case(seed, ctx)` must produce byte-identical fixtures and expected output for a seed, on
   every run and every machine (use `random.Random(<const> + seed)`, never time, hash() of str, or dict
   order you don't control). Seed 0 is the example; seeds 1..n_hidden (default 2) are hidden.
2. **Hidden cases exercise the same features with different data**, including the edge cases. They must never
   test anything the prompt does not state. Each case should contain *all* the traps, so that one case
   alone discriminates.
3. **Expected output comes from an oracle in `challenge.py`**, written independently of `ref/main.py`.
   The oracle may use any Python stdlib module. For format-heavy tasks, prefer generating the input with a
   real producer (zlib, sqlite3, wave, zoneinfo, email, hashlib …) so the input is genuinely valid.
4. **The prompt is the whole contract.** It is language-neutral (use `{LANG}` for the language name), states
   the command-line arguments exactly, defines every output detail (ordering, number formatting, blank
   lines, escaping, what to print on each error), and includes a short worked example of the output.
   Never make the grade depend on something unstated. Avoid floating-point output unless the values are
   exact and the format is pinned (prefer integers, fixed-point via integer arithmetic, or hex digests).
   The harness appends the build/run contract — don't repeat it.
5. **Services** (HTTP/UDP/WebSocket mocks) start in `start(ctx)` on `127.0.0.1` port 0, in daemon threads,
   using only the Python stdlib. They must handle **≥ 16 concurrent clients** (one per test running in
   parallel), so keep them stateless or key all state per connection / per issued token. Pass the base
   URL or port to the program as an argument; select the case's data by URL path (e.g. `/c/<seed>/...`).
   Servers must validate the client's protocol strictly and respond with a clear error (that the program
   would print or that changes its output) when the client gets it wrong — that is where the test bites.
6. **Checkers.** Use `expected=` (exact stdout) whenever possible. Use `check=` only when the result is a
   file or the stdout is legitimately order-free; a checker returns `(ok, message)` and the message is
   shown to the model, so make it specific ("pixel (12,40): expected (255,0,0) got (254,0,0)").
7. **Size and speed.** Inputs small enough that a straightforward correct program in any of the six
   languages finishes in well under the timeout (default 20 s; set `timeout` if needed). Python ref < 2 s.
8. **Difficulty target.** A strong frontier model with thinking should solve it, sometimes needing one or two
   feedback rounds; a ~30B open model one-shot should often fail. The difficulty must come from *substance*
   (spec knowledge, binary layouts, protocol state, edge cases, algorithms) — never from vague wording.
9. **Verify:** `python3 bench.py verify <name> --langs python` must PASS, three times in a row, and
   `python3 bench.py prompt <name> c` must read as a complete spec to someone who has never seen the
   generator. Also run `bench.py try` with a deliberately wrong program and check the feedback is useful and
   does not dump hidden expected output wholesale.

## The six languages (macOS arm64)

Python 3.13 · Java 21 (`javac Main.java`) · Swift 6.4 (`swiftc -O`) · TypeScript on Node 26 via `tsx` · C17
with Apple clang (`cc -O2 -std=c17 main.c -lm`) · Rust 1.96 edition 2021 (`rustc -O`). Standard libraries
plus macOS frameworks only (so C may use CommonCrypto/libSystem; Swift may use Foundation/CryptoKit/SQLite3/
Compression; nothing needs extra link flags). Rust std has no HTTP, JSON, regex, compression or hashing; C has
no JSON or zlib — that asymmetry is deliberate.
