# CLAUDE.md

Coding benchmarks for LLMs in six languages (Python, Java, Swift, TypeScript, C, Rust): **v1** (8 challenges, 48 tests)
and **v2** (20 challenges, 120 tests). Python standard library only; there is no build step. `README.md` covers usage.

## Commands

```bash
python3 bench.py doctor                         # the six toolchains work
python3 bench.py verify [--suite v1|v2] [name]  # grade reference solutions: THE regression test (48/48 and 120/120)
python3 bench.py prompt <challenge> <lang>      # exact prompt a model receives
python3 bench.py try <challenge> <lang> <file>  # grade one program, print the model's feedback
python3 bench.py run --model <id> [--suite v1,v2] [--max-tokens 32768|0] [--runs N] ...
python3 bench.py report|html results/*.json
```

Run `verify` after touching anything under `langbench/`. Both suites must stay all-PASS.

## Layout

- `langbench/suites.py`: one interface (`prompt`, `reply_text`, `extract`, `grade`, `retry_prompt`, `ref_code`) over two
  suites that deliberately keep **their own** prompt contract, code extraction (v1 concatenates every same-language block;
  v2 takes the longest), grading and retry wording. Don't unify them. That would break comparability with published results.
- `langbench/v1/suite.py`: ported verbatim from the original harness (challenge text, fixtures, `compute_*_expected`
  oracles, `build_prompt`, `extract_code`, `run_code`, `check_output`). The only changes are a process-group kill on
  timeout and `system_info`'s expected output being computed at grading time (hostname flips).
- `langbench/v2/harness.py` + `challenges/<name>/challenge.py`: each challenge imports `harness` by path
  (`parents[2]`), so keep that directory structure. New challenges follow `langbench/v2/AUTHORING.md`; references are
  blind-solved per `BLIND_SOLVER.md`.
- `langbench/runner.py`: OpenAI-compatible client, two attempts, scoring, resume (same `RESUME_KEYS` → continue;
  records that failed only on request errors are re-run).
- `langbench/loopguard.py`: streamed client (`stream_chat`) + `LoopGuard`. **Byte-identical to
  `hle-lite/hlelite/loopguard.py`; change both together.** runner.py uses it unless `--no-loop-guard`.
- `langbench/report.py`: grouping by (suite, label), text/markdown tables, McNemar, HTML with inline SVG heat maps
  (CSS variables for light/dark); `--svg-dir` writes standalone SVGs with literal colours (librsvg ignores CSS vars).

## Rules that keep it a fair test

- One neutral prompt per (challenge, language) for every model; only wire-level knobs differ.
- Scoring: 1.0 first try, 0.5 after one feedback round, 0 otherwise. Errors and timeouts stay in the denominator.
- Size `--max-tokens` for the reasoning trace and give every compared model the same cap; "capped" counts
  `finish_reason == "length"`.
- **Loop guard (1.1.0, default on):** a reply caught in a repetition loop is cut, recorded as `finish_reason
  "loop"` / stage `loop`, and the test fails with no retry. Compare only runs with the same `loop_guard` setting
  (it is a resume key). Don't loosen the thresholds without re-running the calibration in loopguard.py's docstring:
  a false positive fails a test that could have passed.
- v1 references use `__FIXTURE_DIR__` / `__PORT__` placeholders (v1 prompts name an absolute temp dir and port).

## Example results

`results/examples/{v1,v2}/` were imported from Incept5's internal runs with hosts, IPs, usernames and local paths
removed. Re-scan for leaks before adding more. `docs/example-report.html` and `docs/img/*.svg` are generated:
`python3 bench.py html results/examples/*/*.json -o docs/example-report.html --svg-dir docs/img`.
Never commit secrets, API keys or full replies from private data.
