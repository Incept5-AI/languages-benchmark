# v2 — design

**Why a v2.** The original coding battery (v1 in this repo, 8 challenges × 6 languages = 48)
is saturated: Qwen3.8-Flash-Next scores 44–46/48 at `medium`, GLM-5.3-Flash 43.5. The last points are noise.
Every one of the 48 cells was re-confirmed solvable on 2026-10-14 (47 by replaying model-written passing
programs, the Swift `file_tree_analysis` cell by a hand-written program). v1 stays untouched so old numbers
stay comparable; v2 is a separate battery.

**What v2 measures.** Whether a model can write a real program from a precise spec in each language: talk to
APIs and protocols, implement LLM tool-calling clients and servers, parse and produce binary formats and
images, and implement spec-defined text processing — with only the standard library. Target: solvable by a
frontier model with thinking (sometimes with one or two feedback rounds); hard for ~30B open models one-shot.

**Harness principles** (from Terminal-Bench, Aider Polyglot, LiveCodeBench, and how Pi / OpenCode / DeepSeek
Harness treat models): one neutral prompt for every model, no tools, no per-model prompt tuning; only the
wire-level differences (thinking switch, effort, reasoning field). Two attempts with deterministic feedback;
report first-try, any-try and well-formedness separately. Every test has a hidden case or two with different
data, so printing the example's answer can't pass. Every task has a reference solution in all six languages,
written from the prompt alone, before any model is scored on it.

## The 20 challenges

| # | name | group | the substance |
|---|---|---|---|
| 1 | rest_client | api | OAuth client-credentials, cursor pagination, 429 + Retry-After, 503 retry, token expiry mid-run |
| 2 | websocket_client | api | RFC 6455 client: handshake + accept check, masking, fragmentation, ping/pong, 16/64-bit lengths, close |
| 3 | dns_resolver | api | DNS over UDP: build queries, parse compression pointers, CNAME chains, AAAA (RFC 5952), TC → TCP retry |
| 4 | tool_agent_openai | tools | OpenAI chat-completions tool loop: parallel tool calls, tool_call_id, arguments-as-string, unknown tool |
| 5 | stream_anthropic | tools | Anthropic Messages SSE: thinking + signature, input_json_delta reassembly, tool_result round trip |
| 6 | mcp_server | tools | MCP server over stdio: JSON-RPC 2.0, initialize, tools/list, tools/call, errors, notifications |
| 7 | bmp_regions | binary | BMP variants (bottom-up/top-down, 1/8/24/32-bit, BITFIELDS) + connected components |
| 8 | png_decode | binary | PNG: chunks + CRC, zlib/inflate (stored/fixed/dynamic), all 5 filters, palette+tRNS, sub-byte depths |
| 9 | png_render | binary | Rasterise a scene with exact integer rules and alpha blending; write a valid PNG (graded by decoding) |
| 10 | wav_dtmf | binary | RIFF/WAVE chunk walking + Goertzel DTMF detection with noise and distractor tones |
| 11 | protobuf_decode | binary | Protobuf wire format from a .proto subset → proto3 JSON mapping (int64 strings, enums, packed, merge) |
| 12 | sqlite_dump | binary | SQLite file format: b-tree pages, interior pages, overflow pages, record serial types, rowid alias |
| 13 | tzif_localtime | binary | TZif v2+ files + POSIX TZ footer rules: UTC→local and local→UTC with gaps/overlaps |
| 14 | jwt_verify | text | JWT/JWK HS256/384/512, strict base64url, raw-segment signing input, ordered rules (**done**) |
| 15 | json_schema | text | JSON Schema 2020-12 subset with $ref, combinators, unicode lengths, pointer output |
| 16 | mime_parse | text | MIME: nested multipart, QP/base64, RFC 2047 words, RFC 2231 filenames, charsets |
| 17 | decimal_ledger | text | Arbitrary-precision decimal ledger, HALF_EVEN, beyond 64-bit and beyond 38 digits |
| 18 | regex_engine | lang | Backtracking-semantics regex matcher that must run in linear time (catastrophic patterns) |
| 19 | mini_interpreter | lang | Interpreter for a small specified language: closures, recursion, lists, strings, errors |
| 20 | patch_apply | lang | Apply git-style unified diffs: multi-file, create/delete, offsets, no-newline markers, rejects |

## Briefs (authors: these are starting points — tighten anything ambiguous, keep the substance)

### 1 rest_client (api)
Args: `<data_dir> <base_url>`. `data_dir/client.json` has `client_id`, `client_secret`. Server under
`<base_url>` (which already includes `/c/<seed>`): `POST /oauth/token` (form-urlencoded
`grant_type=client_credentials`, client id/secret via HTTP Basic auth — server rejects JSON bodies with 415
and bad credentials with 401) → `{"access_token","token_type":"Bearer","expires_in"}`. `GET /orders?limit=N&cursor=C`
with `Authorization: Bearer` → `{"data":[…], "next_cursor": str|null}`. Per token, deterministic misbehaviour:
one page answers 429 with `Retry-After: 1` (retrying sooner gets another 429), one page answers 503 once, and
after K requests the token expires (401 with `WWW-Authenticate: Bearer error="invalid_token"`) — the client
must re-authenticate and resume from the same cursor. Orders: id, customer (unicode names, JSON escapes),
`amount` as a decimal string ("12.30", "-0.05"), currency, status. Output: totals per customer in integer
cents (no floats), sorted; counts by status; final line with the number of orders. Pages ≈ 8–12, timeout ok
with 2 × 1 s waits.

### 2 websocket_client (api)
Args: `<ws_url>` (`ws://127.0.0.1:PORT/c/<seed>`). Client handshake (server validates Upgrade, Connection,
Sec-WebSocket-Version 13, a key that is base64 of 16 bytes; the client must verify Sec-WebSocket-Accept).
Server then sends commands as text messages — some fragmented across continuation frames with a ping
interleaved, some using 16-bit and one using 64-bit payload lengths — e.g. `ADD a b`, `REV text` (reverse by
code point), `SHA1 <n>` followed by a binary message whose SHA-1 hex is the answer, `ECHO`. Client replies
with masked text frames (server rejects unmasked frames with close 1002), answers every ping with a pong
carrying the same payload, and when the server sends close (code + reason) echoes the close. Output: one
line per command `<command> -> <reply>`, then `closed <code> <reason>`. Server ends with close 1002 and a
reason naming the client's first mistake, which changes the output.

### 3 dns_resolver (api)
Args: `<data_dir> <port>`. `queries.txt`: `<name> <TYPE>` lines (A, AAAA, MX, TXT, CNAME). Mock DNS on
127.0.0.1:port UDP **and** TCP. Client sends a standard query (random id, RD=1, one question); server
validates the wire format (FORMERR otherwise) and answers with name compression (pointers to pointers),
CNAME chains in the answer section, multiple RRs, MX, TXT with several character-strings, AAAA addresses
printed in RFC 5952 form, NXDOMAIN, and for some names a truncated UDP reply (TC=1) that must be retried
over TCP (2-byte length prefix). Names answered in mixed case must be printed lower-case. Output per query
fixed format, records in answer order.

### 4 tool_agent_openai (tools)
Args: `<data_dir> <base_url>`. `task.json`: model, system prompt, user message, tool JSON schemas. Local tools
(defined precisely in the prompt) backed by data files: e.g. `inventory_lookup(sku)` over `inventory.csv`,
`convert(amount_cents, from, to)` with integer rate table and HALF_UP rounding, `calc` maybe. Mock
`POST /chat/completions` validates the request strictly (tools present, every assistant `tool_calls` message
echoed back unchanged, each tool message has the matching `tool_call_id` and a string `content`, order) and
returns 400 + error text otherwise. Its scripted replies depend on the tool results it receives: turn 1 two
parallel calls, turn 2 one call (incl. once an unknown tool name the client must answer with the exact error
content the prompt specifies), then a final assistant message quoting the results. Output: `CALL`, `RESULT`,
`FINAL` lines and summed usage.

### 5 stream_anthropic (tools)
Args: `<data_dir> <base_url>`. Client calls `POST /v1/messages` with `x-api-key` (from data dir),
`anthropic-version: 2023-06-01`, `stream: true`, tools. Mock streams SSE (written in arbitrary small chunks
that split lines and UTF-8 sequences): message_start, ping, a thinking block (thinking_delta +
signature_delta), text deltas, a tool_use block whose `input` arrives as input_json_delta fragments split
mid-token, message_delta (stop_reason tool_use, usage), message_stop. Client runs the tool, then sends the
second request with the full assistant content (thinking block with its signature unchanged, text, tool_use)
and a user `tool_result`; mock validates it and streams the final answer. Output: transcript lines + usage.

### 6 mcp_server (tools)
The program *is* an MCP server (2025-06-18) over stdio: newline-delimited JSON-RPC on stdin until EOF,
responses on stdout one per line. Requests include initialize, notifications/initialized (no reply),
tools/list (exact tool definitions given in the prompt incl. inputSchema/outputSchema), tools/call for
stateful and stateless tools (validation failures → result with `isError: true`; unknown tool → JSON-RPC
error -32602), unknown method -32601, unparseable line -32700 with `id: null`, invalid request -32600,
ping, string and numeric ids echoed exactly, notifications never answered. Checker parses each output line as
JSON and compares structurally in order (key order and whitespace free), with a precise message.

### 7 bmp_regions (binary)
Args: `<data_dir>`; several `.bmp` files (sorted by name): 24-bit bottom-up with row padding, 32-bit top-down
(negative height) BI_BITFIELDS with unusual masks, 8-bit and 1-bit palette (palette order not luminance
order), 4-bit maybe. Ink = integer luminance `(299R+587G+114B)/1000 < 128`. Output per file: size, bpp,
number of 8-connected ink components, then each component (sorted by top-most then left-most pixel) with
bbox and pixel count. Unsupported compression (BI_RLE8) → a stated error line.

### 8 png_decode (binary)
Args: `<data_dir>`; PNG files incl. color types 0/2/3/4/6, bit depths 1/2/4/8 (16 optional), tRNS for
palette and gray, all five filter types (craft them), IDAT split into many chunks, ancillary chunks to skip,
deflate streams that are stored, fixed-Huffman (`Z_FIXED`) and dynamic, one file with a bad CRC → stated error
line. Output per file: header facts, chunk sequence, and of the image expanded to 8-bit RGBA: per-channel sums
and the CRC-32 of the RGBA buffer in hex. No interlace (state it).

### 9 png_render (binary output)
Args: `<data_dir>`; `scene.json` with canvas size, background, and shapes drawn in order: rects, circles
(integer centre/radius, inside iff dx²+dy² ≤ r²), triangles (pixel centre test with integer edge functions,
edges inclusive), thick lines maybe; colours RGBA with an exact integer blend formula. Program writes
`out.png` (8-bit RGB, any valid filters/compression) to the cwd and prints a summary line (e.g. distinct
colour count). Checker decodes strictly (signature, CRCs, IHDR, zlib + Adler-32, filters) and compares every
pixel; feedback names the first wrong pixel or the first structural error.

### 10 wav_dtmf (binary)
Args: `<data_dir>`; WAV files: 8 kHz / 16 kHz, mono/stereo (use left channel or mixdown — state which),
16-bit PCM, extra chunks (LIST, odd-sized chunk with pad byte) before/after `fmt`/`data`. Tones aligned to
20 ms blocks; noise; one non-DTMF distractor tone; amplitude twist. State the detection rule precisely
(block length, Goertzel targets, thresholds relative to block energy, minimum duration, merge rule). Output:
digits with start/end ms per file.

### 11 protobuf_decode (binary)
Args: `<data_dir>`; `schema.proto` (proto3 subset: messages, nested messages, enums, repeated, packed and
unpacked, map<string,int32>, oneof, int32/int64/uint32/uint64/sint32/sint64/bool/string/bytes/fixed32/
fixed64/sfixed32/sfixed64/enum), `messages.bin` as varint-length-delimited records of a stated root type.
Output: canonical proto3 JSON per message (lowerCamelCase names, 64-bit ints as strings, enums as names or
number if unknown, bytes base64, defaults omitted, map keys sorted, compact). Semantics: unknown fields
skipped (all wire types), last-wins for scalars, merge for repeated embedded message fields, oneof last-wins.

### 12 sqlite_dump (binary)
Args: `<data_dir>`; `db.sqlite` built by Python's sqlite3 with `page_size=1024` (multi-level table b-trees,
overflow pages for large TEXT/BLOB), INTEGER PRIMARY KEY alias (NULL in the record), all serial types
(0–9, blobs, text), deleted rows/freelist, an index to ignore, UTF-8 text. Output per table (sorted by name):
rows by rowid in a stated literal format (REAL values chosen with exact short representations and a pinned
format). Python may use `sqlite3` — fine.

### 13 tzif_localtime (binary)
Args: `<data_dir>`; TZif files copied verbatim from macOS `/usr/share/zoneinfo` into fixtures (London,
New_York, Lord_Howe (30-min DST), Sao_Paulo (DST abolished), Apia (skipped a day), Kathmandu (+05:45)…).
`queries.txt`: `UTC <zone> <unix>` → local time + offset + abbreviation; `LOCAL <zone> <YYYY-MM-DDTHH:MM:SS>`
→ all matching UTC instants (0 = gap, 2 = overlap). Includes instants beyond the last transition (needs the
POSIX TZ footer rule, incl. `Mm.w.d/time` and negative/odd times) and before the first. Oracle:
`zoneinfo.ZoneInfo.from_file`.

### 15 json_schema (text)
Args: `<data_dir>`; `schema.json` + `instances.json` (array). Subset of 2020-12: type (integer = number with
zero fraction, so 1.0 is an integer), enum, const (deep equality, 1 == 1.0), properties, required,
additionalProperties (bool|schema), patternProperties excluded, items, prefixItems, min/maxItems,
uniqueItems, minimum/maximum/exclusive*, min/maxLength (code points), allOf/anyOf/oneOf/not, if/then/else,
$ref to `#/$defs/...` (recursive). Output per instance: `valid` or the sorted list of
`<instance JSON pointer> <keyword>` errors with precise reporting rules for combinators. Pointer escaping.

### 16 mime_parse (text)
Args: `<data_dir>`; `.eml` files with nested multipart (mixed → alternative → related), quoted-printable
(soft breaks, =XX), base64 with line breaks, charsets utf-8 / iso-8859-1 / windows-1252 (table given for
0x80–0x9F), RFC 2047 encoded-words (B and Q, adjacent words joined), folded headers, RFC 2231 filenames with
continuations and percent-encoding, preamble/epilogue, boundary look-alikes inside bodies. Output: the MIME
tree, decoded subject/from, text part contents (or a prefix + length), attachment name/size/CRC-32.

### 17 decimal_ledger (text)
Args: `<data_dir>`; `ledger.txt` of operations (open, deposit, withdraw, transfer, convert at a rate with up to
12 decimal places, apply interest at a decimal rate, split by ratios with largest-remainder allocation), amounts
up to 10^30 with up to 6 dp. All rounding HALF_EVEN to the currency's minor units at stated points. Output:
balances and a few derived figures. Rejections (insufficient funds, unknown account) with stated messages.

### 18 regex_engine (lang)
Args: `<data_dir>`; `cases.txt` of pattern/subject pairs. Subset: literals, `.`, classes with ranges and
negation, escapes `\d \w \s \.` etc., anchors `^ $`, groups, alternation, `* + ? {m} {m,} {m,n}` and lazy
forms. Output per case: the match a backtracking engine (Perl/Python `re.search`) returns — span and group 1
span, or NOMATCH. Some cases are catastrophic for backtracking (`(a*)*b` on 40 a's, nested alternations), so
the time limit forces a Pike-VM / memoised approach; the language's built-in regex is allowed but will time out
on those. Oracle: Python `re` on the non-catastrophic ones + an own Pike VM cross-checked against `re`.

### 19 mini_interpreter (lang)
Args: `<data_dir>`; `program.tern` — a small language fully specified in the prompt (grammar, precedence,
integer semantics with an overflow error, strings, lists by reference, closures capturing variables by
reference, recursion, while/for, `print`, runtime errors with line numbers terminating the program with a
stated message). The hidden cases are different programs exercising every feature. Oracle: an interpreter
in `challenge.py`.

### 20 patch_apply (lang)
Args: `<data_dir>`; `tree/` of files and `changes.diff` (git-style, several files). Hunks may be offset (search
outward from the stated line: nearest first, earlier before later on a tie — state it), must match exactly
(no fuzz), may create (`--- /dev/null`) or delete files, handle `\ No newline at end of file` on either side,
and some hunks fail (reject; the rest of that file still applies). Output: per file the hunk results
(`applied at L (offset ±k)` / `rejected`) and final content digests (CRC-32 or line count + content for small
files), files sorted.
