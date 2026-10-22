"""sqlite_dump — read a SQLite 3 database file directly and dump every table's rows in a pinned literal format.

Hard parts: the 100-byte header on page 1 (and page 1 itself being an interior page because sqlite_schema
spans several pages), interior/leaf table b-tree pages to depth 3, cell layout and varints (9-byte varints for
huge and negative rowids), record headers (a 2-byte header-size varint for a 64-column table), every serial
type (0-9 including the 6-byte integer and the constants 0/1), overflow pages with the exact local-payload
formula (records sized to sit exactly on the 989/990-byte boundary and on both branches of the K/M rule),
INTEGER PRIMARY KEY aliasing (NULL in the record; `INT PRIMARY KEY` is NOT an alias), records shorter than the
column list after ALTER TABLE ADD COLUMN, a dropped table and deleted rows whose stale cells sit on freelist
pages (a page-scanner prints ghosts), and column names parsed from CREATE TABLE text with commas inside
parentheses and quoted names.

The database is produced by Python's sqlite3 and the oracle reads it back with SQL — independent of any file
parsing. The values are chosen so the pinned formats are exact (dyadic REALs; no integral REAL in a REAL-affinity
column, which SQLite would store as an integer on disk).
"""
import random
import sqlite3
import sys
import zlib
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = """\
Write a {LANG} program that reads a SQLite 3 database file directly and prints the contents of its tables.

The program is run with one argument: a directory containing `db.sqlite`, a database in the standard SQLite 3
file format (as documented in "Database File Format", https://www.sqlite.org/fileformat2.html). It was written by
SQLite with `PRAGMA page_size = 1024` and then closed cleanly (no journal or WAL file exists). Text encoding is
UTF-8, auto-vacuum is off, and there are no WITHOUT ROWID tables. Read the page size and the number of
reserved bytes per page from the file header; do not assume them.

You must handle: page 1 (which starts with the 100-byte file header) being an interior page, interior and leaf
table b-tree pages to any depth, cells whose payload spills onto chains of overflow pages, every record serial
type, and freelist pages and freeblocks left behind by deleted rows and a dropped table (their stale contents
are not part of any table: find rows only by walking each table's b-tree from its root page). Index b-trees,
views and triggers exist and must be ignored.

## Which tables

Read the schema table (`sqlite_schema`, rooted at page 1; columns type, name, tbl_name, rootpage, sql). Dump every
row with type `table` whose `name` does not start with `sqlite_`, in ascending byte-wise order of `name`.

## Columns

The column names come from the table's `sql` text, which has the form `CREATE TABLE <table-name>(<defs>)` (the
table name may be double-quoted; whitespace and line breaks may appear anywhere between tokens). `<defs>` is a
list of column definitions separated by commas that are not inside parentheses (a type such as `NUMERIC(10, 2)`
or a constraint such as `CHECK (x IN (1, 2))` contains commas). There are no table constraints, comments or string
literals. Each definition starts with the column name — a bare identifier, or a double-quoted identifier (print it
without the quotes; it contains no `"`) — optionally followed by a type name and column constraints.

A column is an alias for the rowid if and only if its declared type is the single word `INTEGER` (any letter
case) and its definition contains `PRIMARY KEY`. For such a column the record stores NULL; its value is the row's
rowid. (`INT PRIMARY KEY` is not an alias: that column's value is stored in the record normally.)

A record may contain fewer values than the table has columns (columns added later with `ALTER TABLE ADD COLUMN`);
the missing trailing values are NULL.

## Output

For each table:
```
TABLE <name> rows=<number of rows>
COLUMNS <name1>, <name2>, ...
<rowid>: <value1>, <value2>, ...
```
with one row line per row in ascending rowid order (rowids are signed 64-bit integers and may be negative). Print
one blank line between tables. Values, by storage class (the class is the serial type stored in the file, not the declared column type):

- NULL → `NULL`
- INTEGER → decimal, with a leading `-` if negative (e.g. `-9223372036854775808`)
- REAL → the exact decimal value of the IEEE 754 double, in plain notation without exponent: an optional `-`,
  the integer part without leading zeros (`0` if below 1), `.`, and the fractional digits with trailing zeros
  removed but at least one digit (`2.0`, `-0.375`, `576460752303423488.0`). Every REAL in the files has an exact
  decimal expansion of at most 8 fractional digits.
- TEXT of at most 100 bytes (UTF-8) → in double quotes, with `\\` written as `\\\\`, `"` as `\\"`, newline as `\\n`,
  carriage return as `\\r`, tab as `\\t`, every other byte below 0x20 and 0x7F as `\\xHH` (two uppercase hex
  digits); all other characters as-is (UTF-8).
- BLOB of at most 100 bytes → `X'` + the bytes in uppercase hex + `'` (an empty blob is `X''`).
- TEXT or BLOB longer than 100 bytes → `<TEXT len=N crc32=hhhhhhhh>` or `<BLOB len=N crc32=hhhhhhhh>`, where N is the
  length in bytes and hhhhhhhh is the CRC-32 (the zlib/PNG CRC: reflected polynomial 0xEDB88320, initial value and
  final XOR 0xFFFFFFFF) of those bytes in 8 lowercase hex digits.

Example output (two small tables):
```
TABLE kv rows=2
COLUMNS id, k, v
1: 1, "alpha", X'00FF'
7: 7, "tab\\there", NULL

TABLE notes rows=1
COLUMNS a, weird col, b
-3: 0.5, <TEXT len=150 crc32=0a1b2c3d>, 12
```
"""


# ── value formatting (oracle) ────────────────────────────────────────────────
def fmt_real(x: float) -> str:
    s = format(Decimal(x), "f")
    if "." not in s:
        s += ".0"
    else:
        s = s.rstrip("0")
        if s.endswith("."):
            s += "0"
    if s.startswith("-0.") and x == 0:
        raise AssertionError("negative zero not used")
    return s


def fmt_text_bytes(b: bytes) -> str:
    out = []
    for ch in b.decode("utf-8"):
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20 or o == 0x7F:
            out.append("\\x%02X" % o)
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def fmt_value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return fmt_real(v)
    if isinstance(v, str):
        b = v.encode("utf-8")
        if len(b) > 100:
            return f"<TEXT len={len(b)} crc32={zlib.crc32(b):08x}>"
        return fmt_text_bytes(b)
    if isinstance(v, bytes):
        if len(v) > 100:
            return f"<BLOB len={len(v)} crc32={zlib.crc32(v):08x}>"
        return "X'" + v.hex().upper() + "'"
    raise TypeError(type(v))


# ── data generation ──────────────────────────────────────────────────────────
INTS = [0, 1, -1, 127, -128, 128, 300, -32768, 70000, -8388608, 2**31 - 1, -2**31, 2**40, -2**47, 2**47 - 1,
        2**62, -2**63, 2**63 - 1, 42]
DYADIC = [0.5, -0.375, 1234.125, -1e-0 * 3.75, 0.00390625, 98765.4375, -2.5, 7 / 256, 4503599627370495.5]
INTEGRAL_REALS = [2.0, -7.0, 576460752303423488.0, 1e15, 0.0]
TEXTS = ["", "plain", 'quote " here', "back\\slash", "line\nbreak", "cr\rlf", "tab\tx", "ctl\x01\x1f\x7f",
         "café", "€", "日本語テキスト", "😀 emoji", "mixed ' apostrophe", "a, b, (c)"]


def rtext(rng, lo, hi):
    alphabet = "abcdefghijklmnopqrstuvwxyz ABCDEFG0123456789éü€😀\n\t\"\\"
    n = rng.randint(lo, hi)
    return "".join(rng.choice(alphabet) for _ in range(n))


def text_of_bytes(rng, nbytes):
    """A string whose UTF-8 encoding is exactly nbytes long (mixes 1-, 2- and 3-byte chars)."""
    s, b = [], 0
    while b < nbytes:
        room = nbytes - b
        ch = rng.choice(["a", "Z", "é", "€", " ", "q"]) if room >= 3 else rng.choice(["a", "é"] if room >= 2 else ["a"])
        s.append(ch)
        b += len(ch.encode())
    return "".join(s)


def serial_len(v):
    """(serial type varint length, content length) of a value as SQLite stores it."""
    def vlen(n):
        return 1 if n < 128 else (2 if n < 16384 else 3)
    if v is None:
        return 1, 0
    if isinstance(v, int):
        if v in (0, 1):
            return 1, 0
        for st, (lo, size) in enumerate([(2**7, 1), (2**15, 2), (2**23, 3), (2**31, 4), (2**47, 6)]):
            if -lo <= v < lo:
                return 1, size
        return 1, 8
    if isinstance(v, float):
        return 1, 8
    n = len(v.encode() if isinstance(v, str) else v)
    return vlen(n * 2 + 13), n


def payload_size(vals):
    hs = sum(serial_len(v)[0] for v in vals)
    hdr = hs + 1 if hs + 1 < 128 else hs + 2
    return hdr + sum(serial_len(v)[1] for v in vals)


def build_db(path: Path, rng: random.Random):
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path), isolation_level=None)
    con.execute("PRAGMA page_size = 1024")
    con.execute("PRAGMA auto_vacuum = NONE")
    con.execute("PRAGMA secure_delete = OFF")   # keep stale cells on freelist pages and in freeblocks
    con.execute("PRAGMA journal_mode = DELETE")
    ex = con.execute
    ex("BEGIN")
    # scratch table that will be dropped: its pages go to the freelist with intact stale cells
    ex("CREATE TABLE scratch(id INTEGER PRIMARY KEY, junk TEXT)")
    for i in range(1, 260):
        ex("INSERT INTO scratch VALUES (?, ?)", (i, f"GHOST row {i} " + "x" * rng.randint(10, 40)))

    ex("""CREATE TABLE people(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            age INT CHECK (age IN (1, 2) OR age IS NOT -12345),
            score REAL,
            note)""")
    ids = sorted(rng.sample(range(-50, 400), 40)) + [2**40 + rng.randint(0, 999), 2**63 - 1, -2**63]
    for pid in ids:
        note = rng.choice([None, rng.choice(TEXTS), rng.choice(INTS), rng.choice(INTEGRAL_REALS),
                           rng.choice(DYADIC), bytes(rng.randrange(256) for _ in range(rng.randint(0, 6)))])
        score = rng.choice([None, rng.choice(DYADIC)])
        age = rng.choice([None, rng.randint(0, 120), rng.choice(INTS)])
        ex("INSERT INTO people VALUES (?,?,?,?,?)", (pid, rng.choice(TEXTS), age, score, note))
    ex("DELETE FROM people WHERE id % 7 = 3")

    ex("""CREATE TABLE events (ts INTEGER, kind VARCHAR(12), "unit price" NUMERIC(10, 2),
            payload BLOB, flag)""")
    base = -(2**62) + rng.randint(0, 2**40)
    step = 2**52 + rng.randint(0, 2**30)
    n_ev = 1300
    for i in range(n_ev):
        rid = base + i * step
        price = rng.choice(["12.50", 3.0, 7, "abc", None, 0.25, "-0.125", 1e3])
        payload = rng.choice([None, bytes(rng.randrange(256) for _ in range(rng.randint(0, 48))), rtext(rng, 0, 40),
                                  bytes(rng.randrange(256) for _ in range(rng.randint(40, 90))), rtext(rng, 40, 90)])
        ex("INSERT INTO events(rowid, ts, kind, \"unit price\", payload, flag) VALUES (?,?,?,?,?,?)",
           (rid, rng.choice(INTS + [rng.randint(-10**12, 10**12)]), rng.choice(["click", "view", "buy", "é-vent"]),
            price, payload, rng.choice([0, 1, None, 0.5])))
    ex("DELETE FROM events WHERE (rowid - ?) / ? BETWEEN ? AND ?", (base, step, 300, 480))
    ex("DELETE FROM events WHERE ((rowid - ?) / ?) % 9 = 4", (base, step))
    ex("CREATE INDEX events_kind ON events(kind, ts)")

    ex("CREATE TABLE docs(id INT PRIMARY KEY, body TEXT, data BLOB)")
    # record sizes around the overflow thresholds (U = 1024: X = 989, M = 103, K = M + (P-M) % 1020)
    targets = [988, 989, 990, 991, 1500, 2100, 2124, 5000, 101, 100, 99, 3000]
    for j, P in enumerate(targets):
        did = rng.choice([7, 300, 70000, 2**33]) * (j + 1)
        blob = bytes(rng.randrange(256) for _ in range(rng.choice([0, 3, 50])))
        # choose body length so that the record payload is exactly P bytes
        for blen in range(max(0, P - 80), P + 1):
            body = text_of_bytes(rng, blen)
            if payload_size([did, body, blob]) == P:
                break
        else:
            body = text_of_bytes(rng, max(1, P - 60))
        ex("INSERT INTO docs VALUES (?,?,?)", (did, body, blob))
    ex("INSERT INTO docs VALUES (?,?,?)", (-1, text_of_bytes(rng, 100), bytes(range(101))))
    ex("INSERT INTO docs VALUES (?,?,?)", (-2, text_of_bytes(rng, 101), bytes(range(100))))
    ex("INSERT INTO docs VALUES (?,?,?)", (-3, None, bytes(rng.randrange(256) for _ in range(9000))))

    cols = [f"c{i:02d}" for i in range(64)]
    ex("CREATE TABLE wide(" + ", ".join(f"{c} TEXT" for c in cols) + ")")
    for _ in range(3):
        ex(f"INSERT INTO wide VALUES ({','.join('?' * 64)})", [rtext(rng, 60, 90) for _ in cols])

    ex("CREATE TABLE kv(id INTEGER PRIMARY KEY AUTOINCREMENT, k TEXT UNIQUE, v)")
    for i in range(rng.randint(5, 9)):
        ex("INSERT INTO kv(k, v) VALUES (?, ?)", (f"key{i}", rng.choice(INTS + DYADIC + [None, b"\x00\xff"])))
    ex("DELETE FROM kv WHERE id = 2")

    ex("CREATE TABLE t_altered(a integer primary key, b)")
    for i in range(4):
        ex("INSERT INTO t_altered(b) VALUES (?)", (rng.choice(TEXTS),))
    ex("ALTER TABLE t_altered ADD COLUMN extra TEXT")
    ex("ALTER TABLE t_altered ADD COLUMN \"more stuff\" DOUBLE")
    for i in range(3):
        ex("INSERT INTO t_altered(b, extra, \"more stuff\") VALUES (?,?,?)",
           (rng.choice(TEXTS), rng.choice(TEXTS), rng.choice(DYADIC)))

    ex('CREATE TABLE "Odd Name"( "select" TEXT , "a b"  INTEGER , Integer_Not_Alias INTEGER NOT NULL DEFAULT 0 )')
    for i in range(3):
        ex('INSERT INTO "Odd Name" VALUES (?,?,?)', (rng.choice(TEXTS), rng.choice(INTS), i))
    ex("CREATE TABLE empty_t(x INTEGER PRIMARY KEY, y)")

    ex("CREATE VIEW v_long AS SELECT " + ", ".join(f"p.name AS alias_number_{i}" for i in range(60)) +
       " FROM people p WHERE p.id > 0")
    ex("CREATE TRIGGER trg AFTER INSERT ON kv BEGIN UPDATE kv SET v = v WHERE id = NEW.id; END")
    for i in range(12):
        ex(f"CREATE INDEX pad_index_with_a_rather_long_name_{i} ON events(flag, ts, kind)")
    ex("DROP TABLE scratch")
    ex("COMMIT")
    fl = con.execute("PRAGMA freelist_count").fetchone()[0]
    con.close()
    return fl


def oracle(path: Path) -> str:
    con = sqlite3.connect(str(path))
    con.text_factory = bytes
    tables = [r[0].decode() for r in con.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'")]
    tables.sort(key=lambda s: s.encode())
    blocks = []
    for t in tables:
        q = '"' + t + '"'
        names = [r[1].decode() for r in con.execute(f"PRAGMA table_info({q})")]
        sel = ", ".join(f'typeof("{n}"), "{n}"' for n in names)
        lines = []
        for r in con.execute(f"SELECT rowid, {sel} FROM {q} ORDER BY rowid"):
            vals = []
            for i in range(len(names)):
                typ, v = r[1 + 2 * i].decode(), r[2 + 2 * i]
                if typ == "text":
                    v = v.decode("utf-8")
                elif typ == "blob":
                    v = bytes(v)
                vals.append(fmt_value(v))
            lines.append(f"{r[0]}: " + ", ".join(vals))
        blocks.append("\n".join([f"TABLE {t} rows={len(lines)}", "COLUMNS " + ", ".join(names)] + lines))
    con.close()
    return "\n\n".join(blocks) + "\n"


def check_db(path: Path, freelist: int):
    raw = path.read_bytes()
    assert raw[100] == 0x05, "page 1 should be an interior table page"
    assert freelist > 0
    assert raw.count(b"GHOST row") > 100, "dropped table's stale cells should remain in the file"
    con = sqlite3.connect(str(path))
    # the events table b-tree must be at least 3 levels deep (root and its first child are interior pages)
    root = con.execute("SELECT rootpage FROM sqlite_schema WHERE name='events'").fetchone()[0]
    pg = raw[(root - 1) * 1024:root * 1024]
    assert pg[0] == 0x05
    child = int.from_bytes(pg[int.from_bytes(pg[12:14], "big"):][:4], "big")
    assert raw[(child - 1) * 1024] == 0x05, "events b-tree is not 3 levels deep"
    # no integral value in a REAL-affinity column (SQLite would store it as an integer on disk)
    for t, col in (("people", "score"), ("t_altered", "more stuff")):
        for (v,) in con.execute(f'SELECT "{col}" FROM {t}'):
            assert v is None or (isinstance(v, float) and not v.is_integer()), (t, col, v)
    con.close()


def make_case(seed: int, d: Path):
    rng = random.Random(1200 + seed)
    path = d / "db.sqlite"
    fl = build_db(path, rng)
    check_db(path, fl)
    return oracle(path)


class Challenge(H.Challenge):
    name = "sqlite_dump"
    title = "Read a SQLite 3 database file directly and dump its tables"
    category = "binary"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        d = ctx.data_dir(self, seed)
        exp = make_case(seed, d)
        return H.Case(args=[str(d)], expected=exp)
