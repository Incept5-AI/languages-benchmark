"""Summaries of result files: a text or markdown leaderboard, a paired McNemar test, and a self-contained HTML
report whose heat maps are inline SVG (no JavaScript, renders anywhere, including Quick Look and GitHub).

Runs are grouped by (suite, label): several runs of one configuration (`--runs 3`) appear as one row with the
mean and the min–max range, so run-to-run noise is visible next to every score.
"""
from __future__ import annotations

import html
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

from .suites import LANG_NAMES, LANGS

SUITE_TITLE = {"v1": "v1 · 48 tests", "v2": "v2 · 120 tests"}


# ── Loading and grouping ─────────────────────────────────────────────────────
def load(path):
    d = json.loads(Path(path).read_text())
    d["_file"] = Path(path).name
    d.setdefault("suite", "v2" if len(d.get("records", [])) > 48 else "v1")
    d.setdefault("label", d.get("tag") or Path(path).stem)
    return d


def run_stats(d):
    recs = d["records"]
    att = [a for r in recs for a in r["attempts"]]
    toks = [a.get("completion_tokens") or 0 for a in att]
    rates = [a["tok_per_s"] for a in att if a.get("tok_per_s")]
    lang, chal = defaultdict(float), defaultdict(float)
    nlang, nchal = defaultdict(int), defaultdict(int)
    for r in recs:
        lang[r["language"]] += r["score"]
        nlang[r["language"]] += 1
        chal[r["challenge"]] += r["score"]
        nchal[r["challenge"]] += 1
    return dict(score=sum(r["score"] for r in recs), n=len(recs),
                first=sum(r["score"] == 1.0 for r in recs), any=sum(r["score"] > 0 for r in recs),
                cap=sum(a.get("finish_reason") == "length" for a in att), err=sum(bool(a.get("error")) for a in att),
                minutes=(d.get("elapsed_s") or 0) / 60, tokens=sum(toks),
                tps=statistics.median(rates) if rates else 0,
                lang={l: lang[l] / nlang[l] for l in nlang}, chal={c: chal[c] / nchal[c] for c in nchal},
                lang_n=dict(nlang), chal_n=dict(nchal))


def groups(runs):
    """[(suite, label, [run dict], [stats])], best mean first within each suite."""
    g = defaultdict(list)
    for d in runs:
        g[(d["suite"], d["label"])].append(d)
    out = []
    for (suite, label), ds in g.items():
        ds.sort(key=lambda d: d.get("tag", ""))
        out.append(dict(suite=suite, label=label, runs=ds, stats=[run_stats(d) for d in ds]))
    out.sort(key=lambda r: (r["suite"], -statistics.mean(s["score"] / s["n"] for s in r["stats"])))
    return out


def agg(row):
    st = row["stats"]
    sc = [s["score"] for s in st]
    m = lambda k: statistics.mean(s[k] for s in st)
    d0 = row["runs"][0]
    return dict(mean=statistics.mean(sc), lo=min(sc), hi=max(sc), n=st[0]["n"], runs=len(st), scores=sc,
                first=m("first"), any=m("any"), cap=m("cap"), err=m("err"), minutes=m("minutes"),
                tokens=m("tokens"), tps=m("tps"),
                lang={l: statistics.mean(s["lang"].get(l, 0) for s in st) for l in st[0]["lang"]},
                chal={c: statistics.mean(s["chal"].get(c, 0) for s in st) for c in st[0]["chal"]},
                lang_n=st[0]["lang_n"], chal_n=st[0]["chal_n"],
                effort=d0.get("effort") or ("off" if d0.get("mode") == "no-thinking" else "default"),
                max_tokens=d0.get("max_tokens"), full=bool(d0.get("capped_rerun_max_tokens")), hardware=d0.get("hardware") or "", engine=d0.get("engine") or "",
                model=d0.get("model") or "", note=d0.get("note") or "")


def fmt_score(a):
    s = f"{a['mean']:.1f}"
    if a["runs"] > 1:
        s += f" ({a['lo']:g}–{a['hi']:g})"
    return s


def cap_label(a):
    mt = a["max_tokens"]
    s = "none" if not mt else f"{mt // 1024}k" if mt % 1024 == 0 else str(mt)
    return s + " → full" if a.get("full") else s


# ── Text and markdown ────────────────────────────────────────────────────────
def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def cli_report(files, markdown=False, pair=False):
    runs = [load(f) for f in files]
    rows = groups(runs)
    for suite in sorted({r["suite"] for r in rows}):
        rs = [r for r in rows if r["suite"] == suite]
        aa = [agg(r) for r in rs]
        langs = [l for l in LANGS if any(l in a["lang"] for a in aa)]
        head = ["model", "runs", "score", "%", "1st try", "any", "capped", "errors", "cap", "effort", "min/run",
                "tok/s"] + [LANG_NAMES[l] for l in langs]
        body = []
        for r, a in zip(rs, aa):
            body.append([r["label"], str(a["runs"]), f"{fmt_score(a)}/{a['n']}", f"{100 * a['mean'] / a['n']:.0f}",
                         f"{a['first']:.1f}", f"{a['any']:.1f}", f"{a['cap']:.1f}", f"{a['err']:.1f}",
                         cap_label(a), a["effort"], f"{a['minutes']:.0f}" if a["minutes"] else "—", f"{a['tps']:.0f}"] +
                        [f"{a['lang'].get(l, 0) * a['lang_n'].get(l, 0):.1f}" for l in langs])
        print(f"\n{'### ' if markdown else ''}{SUITE_TITLE.get(suite, suite)}\n")
        print(_table(head, body, markdown))
        chs = sorted({c for a in aa for c in a["chal"]}, key=lambda c: -sum(a["chal"].get(c, 0) for a in aa))
        print(f"\n{'Per challenge' if markdown else 'per challenge'} (mean score per test, 0–1)\n")
        print(_table(["challenge"] + [r["label"] for r in rs],
                     [[c] + [f"{a['chal'].get(c, 0):.2f}" for a in aa] for c in chs], markdown))
    if pair:
        if len(runs) != 2:
            raise SystemExit("--pair needs exactly two result files")
        A, B = runs
        ka = {(r["challenge"], r["language"]): r["score"] == 1.0 for r in A["records"]}
        kb = {(r["challenge"], r["language"]): r["score"] == 1.0 for r in B["records"]}
        common = sorted(set(ka) & set(kb))
        b = sum(ka[k] and not kb[k] for k in common)
        c = sum(kb[k] and not ka[k] for k in common)
        print(f"\npaired first-try passes on {len(common)} tests: only {A['label']}: {b}, only {B['label']}: {c}, "
              f"exact McNemar p = {mcnemar_exact(b, c):.4f}")


def _table(head, body, markdown):
    if markdown:
        lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(head))) + "|"]
        lines += ["| " + " | ".join(row) + " |" for row in body]
        return "\n".join(lines)
    w = [max(len(str(x)) for x in col) for col in zip(head, *body)]
    fmt = lambda row: "  ".join(str(x).ljust(w[i]) if i == 0 else str(x).rjust(w[i]) for i, x in enumerate(row))
    return "\n".join([fmt(head)] + [fmt(r) for r in body])


# ── SVG charts ───────────────────────────────────────────────────────────────
# Sequential blue ramp (light surface): lightest = 0%, darkest = 100%. Dark mode uses the same steps reversed, so
# low scores recede into the dark surface and high scores glow.
RAMP = ["#eef5fd", "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
        "#256abf", "#1c5cab", "#184f95", "#104281"]
RAMP_DARK = ["#1f2733", "#1d3049", "#104281", "#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7",
             "#6da7ec", "#86b6ef", "#9ec5f4", "#cde2fb"]
CSS_SVG = """
.lb-svg text{font:12px -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;fill:var(--ink-2)}
.lb-svg .t{fill:var(--ink-1);font-weight:600}
.lb-svg .lab{fill:var(--ink-1)}
.lb-svg .grid{stroke:var(--grid)}
.lb-svg .bar{fill:var(--series-1)}
.lb-svg .dot{fill:var(--surface);stroke:var(--ink-1);stroke-width:1.5}
""" + "".join(f".lb-svg .h{i}{{fill:var(--h{i})}} .lb-svg .x{i}{{fill:var(--hx{i})}}\n" for i in range(len(RAMP)))


def _theme_vars(dark=False):
    ramp = RAMP_DARK if dark else RAMP
    v = {"--surface": "#1a1a19" if dark else "#fcfcfb", "--ink-1": "#f0efec" if dark else "#0b0b0b",
         "--ink-2": "#c3c2b7" if dark else "#52514e", "--grid": "#3a3936" if dark else "#e4e3df",
         "--series-1": "#3987e5" if dark else "#2a78d6"}
    for i, c in enumerate(ramp):
        v[f"--h{i}"] = c
        light_cell = (i < 7) != dark
        v[f"--hx{i}"] = "#0b0b0b" if light_cell else "#ffffff"
    return v


def _step(frac):
    return max(0, min(len(RAMP) - 1, round(frac * (len(RAMP) - 1))))


def _esc(s):
    return html.escape(str(s))


def _text_w(s, px=7.0):
    return len(str(s)) * px


def heatmap(rows, cols, value, label, title, max_of, cell_w=None, rotate=False):
    """rows/cols: lists of display names; value(i, j) -> score (or None); max_of(i, j) -> the max for that cell."""
    cw = cell_w or (46 if rotate else max(64, int(max(_text_w(c, 6.6) for c in cols)) + 14))
    ch = 30
    lw = int(max(_text_w(r) for r in rows)) + 16
    top = int(max(_text_w(c, 6.2) for c in cols) * 0.72) + 24 if rotate else 40
    W = lw + cw * len(cols) + (int(max(_text_w(c, 6.2) for c in cols) * 0.72) if rotate else 8)
    H = top + ch * len(rows) + 8
    s = [f'<svg class="lb-svg" viewBox="0 0 {W} {H}" width="{W}" style="max-width:100%;height:auto;aspect-ratio:{W}/{H}" '
         f'role="img" aria-label="{_esc(title)}">',
         f'<rect width="{W}" height="{H}" style="fill:var(--surface)"/>']
    for j, c in enumerate(cols):
        x = lw + cw * j + cw / 2
        if rotate:
            s.append(f'<text class="t" x="{x:.0f}" y="{top - 8}" transform="rotate(-45 {x:.0f} {top - 8})">'
                     f'{_esc(c)}</text>')
        else:
            s.append(f'<text class="t" x="{x:.0f}" y="{top - 12}" text-anchor="middle">{_esc(c)}</text>')
    for i, r in enumerate(rows):
        y = top + ch * i
        s.append(f'<text class="lab" x="{lw - 10}" y="{y + ch / 2 + 4:.0f}" text-anchor="end">{_esc(r)}</text>')
        for j in range(len(cols)):
            v = value(i, j)
            if v is None:
                continue
            mx = max_of(i, j) or 1
            k = _step(v / mx)
            x = lw + cw * j
            s.append(f'<g><title>{_esc(r)} · {_esc(cols[j])}: {v:g} of {mx:g} ({100 * v / mx:.0f}%)</title>'
                     f'<rect class="h{k}" x="{x + 1}" y="{y + 1}" width="{cw - 2}" height="{ch - 2}" rx="4"/>'
                     f'<text class="x{k}" x="{x + cw / 2:.0f}" y="{y + ch / 2 + 4:.0f}" text-anchor="middle">'
                     f'{label(v, mx)}</text></g>')
    s.append("</svg>")
    return "".join(s)


def leaderboard_chart(rows, aa):
    """Mean score as a bar (0–100%), each run as a hollow dot, so the spread between runs is visible."""
    lw = int(max(_text_w(r["label"]) for r in rows)) + 16
    pw, rh, top = 460, 30, 26
    W, H = lw + pw + 120, top + rh * len(rows) + 10
    X = lambda f: lw + f * pw
    s = [f'<svg class="lb-svg" viewBox="0 0 {W} {H}" width="{W}" style="max-width:100%;height:auto;aspect-ratio:{W}/{H}" '
         f'role="img" aria-label="Score by model">', f'<rect width="{W}" height="{H}" style="fill:var(--surface)"/>']
    for t in range(0, 101, 20):
        s.append(f'<line class="grid" x1="{X(t / 100):.1f}" x2="{X(t / 100):.1f}" y1="{top - 6}" y2="{H - 6}"/>'
                 f'<text x="{X(t / 100):.1f}" y="{top - 10}" text-anchor="middle">{t}%</text>')
    for i, (r, a) in enumerate(zip(rows, aa)):
        y = top + rh * i
        f = a["mean"] / a["n"]
        bw = max(4.0, f * pw)
        s.append(f'<text class="lab" x="{lw - 10}" y="{y + rh / 2 + 4:.0f}" text-anchor="end">{_esc(r["label"])}</text>'
                 f'<g><title>{_esc(r["label"])}: {fmt_score(a)} of {a["n"]}</title>'
                 f'<path class="bar" d="M{lw},{y + 7} h{bw - 4:.1f} a4,4 0 0 1 4,4 v{rh - 22} a4,4 0 0 1 -4,4 '
                 f'h{-(bw - 4):.1f} z"/></g>')
        if a["runs"] > 1:
            for k, v in enumerate(a["scores"]):
                s.append(f'<circle class="dot" cx="{X(v / a["n"]):.1f}" cy="{y + rh / 2:.1f}" r="4">'
                         f'<title>run {k + 1}: {v:g}</title></circle>')
        s.append(f'<text class="lab" x="{X(max(f, max(a["scores"]) / a["n"])) + 10:.1f}" y="{y + rh / 2 + 4:.0f}">'
                 f'{100 * f:.0f}% · {a["mean"]:.1f}/{a["n"]}</text>')
    s.append("</svg>")
    return "".join(s)


def _standalone(svg):
    """The same SVG as a file of its own (light theme), for READMEs and slides. Colours are written out literally,
    because several SVG renderers (librsvg, some image viewers) do not resolve CSS variables."""
    v = _theme_vars(False)
    lit = lambda t: re.sub(r"var\((--[\w-]+)\)", lambda m: v[m.group(1)], t)
    head, rest = svg.split(">", 1)
    head = head.replace('<svg class="lb-svg"', '<svg xmlns="http://www.w3.org/2000/svg" class="lb-svg"')
    head = re.sub(r' style="[^"]*"', "", head)
    return f"{head}><style>{lit(CSS_SVG)}</style>{lit(rest)}"


# ── HTML page ────────────────────────────────────────────────────────────────
def suite_charts(rows):
    aa = [agg(r) for r in rows]
    langs = [l for l in LANGS if any(l in a["lang"] for a in aa)]
    names = [r["label"] for r in rows]
    lang_svg = heatmap(names, [LANG_NAMES[l] for l in langs],
                       lambda i, j: (aa[i]["lang"][langs[j]] * aa[i]["lang_n"][langs[j]]
                                     if langs[j] in aa[i]["lang"] else None),
                       lambda v, mx: f"{100 * v / mx:.0f}%", "Score by language",
                       lambda i, j: aa[i]["lang_n"].get(langs[j], 1))
    chs = sorted({c for a in aa for c in a["chal"]}, key=lambda c: (-sum(a["chal"].get(c, 0) for a in aa), c))
    chal_svg = heatmap(names, chs,
                       lambda i, j: (aa[i]["chal"][chs[j]] * aa[i]["chal_n"][chs[j]]
                                     if chs[j] in aa[i]["chal"] else None),
                       lambda v, mx: f"{100 * v / mx:.0f}", "Score by challenge (%)",
                       lambda i, j: aa[i]["chal_n"].get(chs[j], 1), rotate=True)
    return aa, leaderboard_chart(rows, aa), lang_svg, chal_svg


def cli_html(files, output, title, svg_dir=""):
    runs = [load(f) for f in files]
    rows = groups(runs)
    parts = []
    for suite in sorted({r["suite"] for r in rows}):
        rs = [r for r in rows if r["suite"] == suite]
        aa, board, lang_svg, chal_svg = suite_charts(rs)
        if svg_dir:
            d = Path(svg_dir)
            d.mkdir(parents=True, exist_ok=True)
            for name, svg in (("leaderboard", board), ("languages", lang_svg), ("challenges", chal_svg)):
                (d / f"{suite}-{name}.svg").write_text(_standalone(svg))
        trs = "".join(
            f"<tr><td class=l>{_esc(r['label'])}</td><td>{a['runs']}</td><td>{fmt_score(a)}/{a['n']}</td>"
            f"<td>{a['first']:.1f}</td><td>{a['cap']:.1f}</td><td>{a['err']:.1f}</td><td>{cap_label(a)}</td>"
            f"<td>{_esc(a['effort'])}</td><td>{f"{a['minutes']:.0f}" if a['minutes'] else "—"}</td><td>{a['tps']:.0f}</td>"
            f"<td class=l>{_esc(a['engine'])}{' · ' if a['engine'] and a['hardware'] else ''}{_esc(a['hardware'])}</td></tr>"
            for r, a in zip(rs, aa))
        parts.append(f"""<section><h2>{_esc(SUITE_TITLE.get(suite, suite))}</h2>
<h3>Score</h3><p class=note>Bar: mean score. Dots: individual runs.</p>{board}
<div class=scroll><table><tr><th class=l>Model</th><th>Runs</th><th>Score</th><th>1st try</th><th>Capped</th>
<th>Errors</th><th>Cap</th><th>Effort</th><th>Min/run</th><th>tok/s</th><th class=l>Engine · hardware</th></tr>{trs}</table></div>
<h3>By language</h3><p class=note>Percent of the available score, averaged over runs.</p><div class=scroll>{lang_svg}</div>
<h3>By challenge</h3><p class=note>Percent of the available score (all six languages), averaged over runs. Easiest challenges first.</p>
<div class=scroll>{chal_svg}</div></section>""")
    light = ";".join(f"{k}:{v}" for k, v in _theme_vars(False).items())
    dark = ";".join(f"{k}:{v}" for k, v in _theme_vars(True).items())
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)}</title><style>
:root{{{light}}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{{dark}}}}}
:root[data-theme="dark"]{{{dark}}}
body{{margin:0;background:var(--surface);color:var(--ink-1);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:20px;margin:40px 0 8px;border-top:1px solid var(--grid);padding-top:24px}}
h3{{font-size:15px;margin:24px 0 6px}} .note,.sub{{color:var(--ink-2);font-size:13px;margin:0 0 8px}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;font-size:13px;margin:12px 0;font-variant-numeric:tabular-nums}}
th,td{{padding:5px 10px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}}
th{{color:var(--ink-2);font-weight:600}} .l{{text-align:left}}
{CSS_SVG}</style></head><body><main><h1>{_esc(title)}</h1>
<p class=sub>Scoring: 1.0 first try, 0.5 after one round of feedback, 0 otherwise. Request errors and timeouts count as
failures. "Capped" counts attempts that hit the token cap.</p>
{''.join(parts)}</main></body></html>"""
    Path(output).write_text(page)
    print(f"→ {output}" + (f" (+ SVGs in {svg_dir})" if svg_dir else ""))
