"""v1: the 48-test battery (8 challenges x 6 languages).

Ported from Incept5's original harness. The challenge text, fixtures, expected-output oracles, prompt contract,
code extraction and output comparison are unchanged, so new results line up with the published ones. Only the
local-inference code (MLX, LM Studio) was removed: models are reached through runner.py's OpenAI-compatible
client instead.

Every test is one program, run once against fixed data; the expected output is computed by the oracles below.
"""
import hashlib
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


def _java_bin() -> str:
    """Directory holding javac/java 21+. LB_JAVA_BIN wins; then Homebrew's openjdk@21; then whatever is on PATH."""
    if os.environ.get("LB_JAVA_BIN"):
        return os.environ["LB_JAVA_BIN"]
    brew = "/opt/homebrew/opt/openjdk@21/bin"
    if os.path.isdir(brew):
        return brew
    javac = shutil.which("javac")
    return str(Path(javac).parent) if javac else ""


JAVA_HOME_BIN = _java_bin()
TIMEOUT_SECONDS = 30
TEST_SERVER_PORT = int(os.environ.get("LB_V1_PORT", "18923"))

# Target platform string for native-language prompts (C/Rust). Detected at import
# so it stays correct on whatever host runs the benchmark.
_OS_NAME = {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}.get(
    _platform.system(), _platform.system())
TARGET_PLATFORM = f"{_OS_NAME} ({_platform.system()}) on {_platform.machine()}"


# ── HTTP Test Server ───────────────────────────────────────────────────────
PRODUCTS_DATA = [
    {"id": 1, "name": "Wireless Mouse", "price": 29.99, "category": "electronics", "stock": 150},
    {"id": 2, "name": "Mechanical Keyboard", "price": 89.95, "category": "electronics", "stock": 75},
    {"id": 3, "name": "USB-C Hub", "price": 45.50, "category": "electronics", "stock": 200},
    {"id": 4, "name": "Standing Desk", "price": 349.00, "category": "furniture", "stock": 30},
    {"id": 5, "name": "Monitor Arm", "price": 67.80, "category": "furniture", "stock": 90},
    {"id": 6, "name": "Desk Lamp", "price": 23.40, "category": "lighting", "stock": 300},
    {"id": 7, "name": "Webcam HD", "price": 54.99, "category": "electronics", "stock": 120},
    {"id": 8, "name": "Noise-Cancelling Headphones", "price": 199.99, "category": "audio", "stock": 60},
    {"id": 9, "name": "Mousepad XL", "price": 15.00, "category": "accessories", "stock": 500},
    {"id": 10, "name": "Cable Management Kit", "price": 12.99, "category": "accessories", "stock": 250},
]

class TestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress logging

    def do_GET(self):
        if self.path == "/api/products":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(PRODUCTS_DATA).encode())
        elif self.path == "/api/categories":
            cats = {}
            for p in PRODUCTS_DATA:
                c = p["category"]
                if c not in cats:
                    cats[c] = {"count": 0, "total_value": 0.0}
                cats[c]["count"] += 1
                cats[c]["total_value"] += p["price"] * p["stock"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(cats).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/report":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            # Return a summary
            summary = {
                "received_count": len(data) if isinstance(data, list) else 1,
                "status": "ok",
                "checksum": hashlib.md5(body).hexdigest()[:8],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(summary).encode())
        else:
            self.send_response(404)
            self.end_headers()

_test_server = None
_test_server_port = TEST_SERVER_PORT

def start_test_server():
    global _test_server, _test_server_port
    port = TEST_SERVER_PORT
    for attempt in range(10):
        try:
            _test_server = HTTPServer(("127.0.0.1", port), TestHandler)
            break
        except OSError as e:
            if "Address already in use" in str(e) or e.errno == 48:
                port += 1
            else:
                raise
    else:
        raise RuntimeError(f"Could not find a free port after 10 attempts starting from {TEST_SERVER_PORT}")
    _test_server_port = port
    t = threading.Thread(target=_test_server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)

def stop_test_server():
    global _test_server
    if _test_server:
        _test_server.shutdown()


# ── Fixture Data (module-level constants) ─────────────────────────────────
CONFIG_A = {
    "database": {
        "host": "localhost",
        "port": 5432,
        "credentials": {"user": "admin", "password": "old_pass"},
        "options": {"timeout": 30, "retries": 3}
    },
    "features": ["logging", "caching", "auth"],
    "version": "1.0.0",
    "debug": False
}

CONFIG_B = {
    "database": {
        "port": 5433,
        "credentials": {"password": "new_pass", "ssl": True},
        "replicas": ["replica1.db", "replica2.db"]
    },
    "features": ["caching", "monitoring", "auth", "metrics"],
    "version": "2.0.0",
    "logging": {"level": "info", "file": "/var/log/app.log"}
}

EMPLOYEES_CSV = """employee_id,name,department,salary,start_date,rating
E001,Alice Chen,Engineering,95000,2019-03-15,4.5
E002,Bob Martinez,Marketing,72000,2020-07-01,3.8
E003,Carol Wu,Engineering,105000,2018-01-10,4.9
E004,David Kim,Sales,68000,2021-06-20,3.2
E005,Eva Johansson,Engineering,98000,2019-11-05,4.2
E006,Frank Okafor,Marketing,75000,2020-02-28,4.0
E007,Grace Liu,Sales,71000,2019-08-14,3.9
E008,Hassan Patel,Engineering,110000,2017-05-22,4.7
E009,Iris Nakamura,HR,82000,2020-09-01,4.1
E010,James Brown,Sales,65000,2022-01-15,3.5
E011,Karen Schmidt,HR,78000,2019-04-10,4.3
E012,Leo Rossi,Marketing,69000,2021-03-08,3.6
"""

EMPLOYEE_RECORDS = [
    {"department": "Engineering", "name": "Alice Chen", "salary": 95000},
    {"department": "Marketing", "name": "Bob Martinez", "salary": 72000},
    {"department": "Engineering", "name": "Carol Wu", "salary": 105000},
    {"department": "Sales", "name": "David Kim", "salary": 68000},
    {"department": "Engineering", "name": "Eva Johansson", "salary": 98000},
    {"department": "Marketing", "name": "Frank Okafor", "salary": 75000},
    {"department": "Sales", "name": "Grace Liu", "salary": 71000},
    {"department": "Engineering", "name": "Hassan Patel", "salary": 110000},
    {"department": "HR", "name": "Iris Nakamura", "salary": 82000},
    {"department": "Sales", "name": "James Brown", "salary": 65000},
    {"department": "HR", "name": "Karen Schmidt", "salary": 78000},
    {"department": "Marketing", "name": "Leo Rossi", "salary": 69000},
]

LOG_DATA = """[2024-01-15 08:23:01] INFO (auth) - User login successful: user_id=1042
[2024-01-15 08:23:15] DEBUG (db) - Query executed in 23ms: SELECT * FROM users
[2024-01-15 08:24:02] ERROR (auth) - Authentication failed: invalid token for user_id=2001
[2024-01-15 08:24:30] WARN (cache) - Cache miss rate above threshold: 45%
[2024-01-15 09:01:00] INFO (api) - Request processed: GET /users 200 45ms
[2024-01-15 09:01:45] ERROR (db) - Connection pool exhausted: max_connections=100
[2024-01-15 09:02:10] INFO (auth) - User login successful: user_id=1055
[2024-01-15 09:15:30] ERROR (api) - Request failed: POST /orders 500 internal server error
[2024-01-15 10:00:00] INFO (scheduler) - Cron job started: cleanup_sessions
[2024-01-15 10:00:05] WARN (scheduler) - Job running longer than expected: cleanup_sessions
[2024-01-15 10:05:22] INFO (scheduler) - Cron job completed: cleanup_sessions duration=322s
[2024-01-15 10:30:00] ERROR (auth) - Authentication failed: expired token for user_id=3010
[2024-01-15 10:31:10] ERROR (auth) - Authentication failed: account locked for user_id=4099
[2024-01-15 10:30:45] DEBUG (cache) - Cache invalidation: keys_removed=1523
[2024-01-15 11:00:01] INFO (api) - Request processed: GET /products 200 12ms
[2024-01-15 11:15:00] ERROR (db) - Deadlock detected: table=orders, transaction_id=88901
[2024-01-15 11:15:01] WARN (db) - Transaction rolled back: transaction_id=88901
[2024-01-15 11:30:00] INFO (api) - Request processed: PUT /users/1042 200 67ms
[2024-01-15 12:00:00] INFO (auth) - User login successful: user_id=1042
[2024-01-15 12:45:30] ERROR (api) - Request failed: DELETE /products/99 404 not found
[2024-01-15 13:00:00] INFO (scheduler) - Cron job started: generate_reports
"""

LOG_ENTRIES = [
    ("08", "INFO", "auth", "User login successful: user_id=1042"),
    ("08", "DEBUG", "db", "Query executed in 23ms: SELECT * FROM users"),
    ("08", "ERROR", "auth", "Authentication failed: invalid token for user_id=2001"),
    ("08", "WARN", "cache", "Cache miss rate above threshold: 45%"),
    ("09", "INFO", "api", "Request processed: GET /users 200 45ms"),
    ("09", "ERROR", "db", "Connection pool exhausted: max_connections=100"),
    ("09", "INFO", "auth", "User login successful: user_id=1055"),
    ("09", "ERROR", "api", "Request failed: POST /orders 500 internal server error"),
    ("10", "INFO", "scheduler", "Cron job started: cleanup_sessions"),
    ("10", "WARN", "scheduler", "Job running longer than expected: cleanup_sessions"),
    ("10", "INFO", "scheduler", "Cron job completed: cleanup_sessions duration=322s"),
    ("10", "ERROR", "auth", "Authentication failed: expired token for user_id=3010"),
    ("10", "ERROR", "auth", "Authentication failed: account locked for user_id=4099"),
    ("10", "DEBUG", "cache", "Cache invalidation: keys_removed=1523"),
    ("11", "INFO", "api", "Request processed: GET /products 200 12ms"),
    ("11", "ERROR", "db", "Deadlock detected: table=orders, transaction_id=88901"),
    ("11", "WARN", "db", "Transaction rolled back: transaction_id=88901"),
    ("11", "INFO", "api", "Request processed: PUT /users/1042 200 67ms"),
    ("12", "INFO", "auth", "User login successful: user_id=1042"),
    ("12", "ERROR", "api", "Request failed: DELETE /products/99 404 not found"),
    ("13", "INFO", "scheduler", "Cron job started: generate_reports"),
]

GRAPH_DATA = {
    "nodes": ["A", "B", "C", "D", "E", "F"],
    "edges": [
        {"from": "A", "to": "B", "weight": 4},
        {"from": "A", "to": "C", "weight": 2},
        {"from": "B", "to": "D", "weight": 3},
        {"from": "B", "to": "C", "weight": 1},
        {"from": "C", "to": "D", "weight": 8},
        {"from": "C", "to": "E", "weight": 10},
        {"from": "D", "to": "E", "weight": 2},
        {"from": "D", "to": "F", "weight": 5},
        {"from": "E", "to": "F", "weight": 3},
    ]
}

GRAPH_EDGES_TUPLES = [
    ("A", "B", 4), ("A", "C", 2),
    ("B", "D", 3), ("B", "C", 1),
    ("C", "D", 8), ("C", "E", 10),
    ("D", "E", 2), ("D", "F", 5),
    ("E", "F", 3),
]

GRAPH_NODES = ["A", "B", "C", "D", "E", "F"]

FILES_CONTENT = {
    "src/main.py": "import sys\n\ndef main():\n    print('Hello')\n\nif __name__ == '__main__':\n    main()\n",
    "src/utils/helpers.py": "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n",
    "src/utils/validators.py": "import re\n\ndef is_email(s):\n    return bool(re.match(r'^[\\w.-]+@[\\w.-]+\\.\\w+$', s))\n\ndef is_positive(n):\n    return n > 0\n",
    "src/models/user.py": "class User:\n    def __init__(self, name, email):\n        self.name = name\n        self.email = email\n\n    def __repr__(self):\n        return f'User({self.name})'\n",
    "tests/test_helpers.py": "from src.utils.helpers import add, multiply\n\nassert add(2, 3) == 5\nassert multiply(4, 5) == 20\nprint('All tests passed')\n",
    "docs/README.md": "# Project\n\nThis is a sample project for benchmarking.\n\n## Features\n\n- Feature A\n- Feature B\n- Feature C\n",
    "docs/CHANGELOG.md": "# Changelog\n\n## v1.0.0\n- Initial release\n\n## v1.1.0\n- Added helpers\n- Fixed bugs\n",
}


# ── Fixture Creation ───────────────────────────────────────────────────────
def create_fixtures(base_dir: str):
    """Create all test fixture files."""
    # ── JSON merge fixtures ──
    with open(os.path.join(base_dir, "config_a.json"), "w") as f:
        json.dump(CONFIG_A, f, indent=2)
    with open(os.path.join(base_dir, "config_b.json"), "w") as f:
        json.dump(CONFIG_B, f, indent=2)

    # ── CSV fixture ──
    with open(os.path.join(base_dir, "employees.csv"), "w") as f:
        f.write(EMPLOYEES_CSV.strip() + "\n")

    # ── Log file fixture ──
    with open(os.path.join(base_dir, "app.log"), "w") as f:
        f.write(LOG_DATA.strip() + "\n")

    # ── Graph fixture (weighted adjacency list) ──
    with open(os.path.join(base_dir, "graph.json"), "w") as f:
        json.dump(GRAPH_DATA, f, indent=2)

    # ── Directory tree for file system challenge ──
    tree_dir = os.path.join(base_dir, "project_tree")
    os.makedirs(os.path.join(tree_dir, "src", "utils"), exist_ok=True)
    os.makedirs(os.path.join(tree_dir, "src", "models"), exist_ok=True)
    os.makedirs(os.path.join(tree_dir, "tests"), exist_ok=True)
    os.makedirs(os.path.join(tree_dir, "docs"), exist_ok=True)

    for rel_path, content in FILES_CONTENT.items():
        full_path = os.path.join(tree_dir, rel_path)
        with open(full_path, "w") as f:
            f.write(content)



# ── Expected Output Computation ────────────────────────────────────────────

def compute_json_merge_expected():
    """Deep merge config_a and config_b with rules:
    - Objects: merge recursively
    - Arrays: concatenate, remove duplicates (preserve order from A then new from B)
    - Scalars: B overrides A
    Output: merged JSON with keys sorted at all levels, pretty-printed with 2-space indent.
    """
    def deep_merge(a, b):
        result = {}
        all_keys = list(a.keys()) + [k for k in b.keys() if k not in a]
        for key in all_keys:
            if key in a and key in b:
                va, vb = a[key], b[key]
                if isinstance(va, dict) and isinstance(vb, dict):
                    result[key] = deep_merge(va, vb)
                elif isinstance(va, list) and isinstance(vb, list):
                    # Concatenate, dedup preserving order
                    seen = []
                    for item in va + vb:
                        if item not in seen:
                            seen.append(item)
                    result[key] = seen
                else:
                    result[key] = vb  # B overrides
            elif key in a:
                result[key] = a[key]
            else:
                result[key] = b[key]
        return result

    merged = deep_merge(CONFIG_A, CONFIG_B)

    def sort_keys(obj):
        if isinstance(obj, dict):
            return {k: sort_keys(v) for k, v in sorted(obj.items())}
        elif isinstance(obj, list):
            return [sort_keys(i) for i in obj]
        return obj

    return json.dumps(sort_keys(merged), indent=2)


def compute_csv_expected():
    """Process employees.csv:
    1. Average salary by department (sorted alphabetically)
    2. Highest paid employee name and salary
    3. Departments with more than 2 employees
    4. Total salary budget
    Output format is fixed text.
    """
    employees = EMPLOYEE_RECORDS

    # Average by dept
    dept_salaries = {}
    for e in employees:
        dept_salaries.setdefault(e["department"], []).append(e["salary"])

    lines = ["=== Salary Report ===", ""]
    lines.append("Average Salary by Department:")
    for dept in sorted(dept_salaries.keys()):
        avg = sum(dept_salaries[dept]) / len(dept_salaries[dept])
        lines.append(f"  {dept}: ${avg:,.2f}")

    # Highest paid
    top = max(employees, key=lambda e: e["salary"])
    lines.append("")
    lines.append(f"Highest Paid: {top['name']} (${top['salary']:,})")

    # Departments with >2
    big_depts = sorted([d for d, s in dept_salaries.items() if len(s) > 2])
    lines.append("")
    lines.append("Departments with >2 employees:")
    for d in big_depts:
        lines.append(f"  {d} ({len(dept_salaries[d])} employees)")

    # Total
    total = sum(e["salary"] for e in employees)
    lines.append("")
    lines.append(f"Total Budget: ${total:,}")

    return "\n".join(lines)


def compute_log_expected():
    """Parse app.log and output:
    - Count by log level (sorted alphabetically)
    - Component with most errors
    - All unique error messages (sorted)
    - Busiest hour (most log entries)
    """
    log_entries = LOG_ENTRIES

    # Count by level
    level_counts = {}
    for _, level, _, _ in log_entries:
        level_counts[level] = level_counts.get(level, 0) + 1

    # Component with most errors
    error_by_component = {}
    error_messages = set()
    for _, level, comp, msg in log_entries:
        if level == "ERROR":
            error_by_component[comp] = error_by_component.get(comp, 0) + 1
            error_messages.add(msg)

    most_errors_comp = max(error_by_component, key=error_by_component.get)

    # Busiest hour
    hour_counts = {}
    for hour, _, _, _ in log_entries:
        hour_counts[hour] = hour_counts.get(hour, 0) + 1
    busiest = max(hour_counts, key=hour_counts.get)

    lines = ["=== Log Analysis ===", ""]
    lines.append("Entries by Level:")
    for level in sorted(level_counts.keys()):
        lines.append(f"  {level}: {level_counts[level]}")

    lines.append("")
    lines.append(f"Component with Most Errors: {most_errors_comp} ({error_by_component[most_errors_comp]} errors)")

    lines.append("")
    lines.append("Unique Error Messages:")
    for msg in sorted(error_messages):
        lines.append(f"  - {msg}")

    lines.append("")
    lines.append(f"Busiest Hour: {busiest}:00 ({hour_counts[busiest]} entries)")

    return "\n".join(lines)


def compute_http_expected():
    """
    1. GET /api/products
    2. Filter products where price > 50, sort by name ascending
    3. For each filtered product, print: "name: $price"
    4. Print total count and sum of prices of filtered products
    """
    filtered = sorted(
        [p for p in PRODUCTS_DATA if p["price"] > 50],
        key=lambda p: p["name"]
    )

    lines = ["=== Filtered Products (price > $50.00) ===", ""]
    for p in filtered:
        lines.append(f"{p['name']}: ${p['price']:.2f}")

    total_price = sum(p["price"] for p in filtered)
    lines.append("")
    lines.append(f"Count: {len(filtered)}")
    lines.append(f"Total: ${total_price:.2f}")

    return "\n".join(lines)


def compute_graph_expected():
    """Dijkstra shortest path from A to all nodes."""
    edges = GRAPH_EDGES_TUPLES
    nodes = GRAPH_NODES

    # Build adjacency (undirected)
    adj = {n: [] for n in nodes}
    for u, v, w in edges:
        adj[u].append((v, w))
        adj[v].append((u, w))

    # Dijkstra from A
    import heapq
    dist = {n: float('inf') for n in nodes}
    dist["A"] = 0
    pq = [(0, "A")]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                heapq.heappush(pq, (dist[v], v))

    lines = ["=== Shortest Paths from A ===", ""]
    for n in sorted(nodes):
        lines.append(f"A -> {n}: {dist[n]}")

    return "\n".join(lines)


def compute_filetree_expected():
    """Walk project_tree, for each file compute line count and char count.
    Group by extension, output summary sorted by extension.
    """
    files_content = FILES_CONTENT

    # Group by extension
    ext_stats = {}
    for path, content in sorted(files_content.items()):
        ext = os.path.splitext(path)[1]
        line_count = len(content.splitlines())
        char_count = len(content)
        if ext not in ext_stats:
            ext_stats[ext] = {"files": [], "total_lines": 0, "total_chars": 0}
        ext_stats[ext]["files"].append((path, line_count, char_count))
        ext_stats[ext]["total_lines"] += line_count
        ext_stats[ext]["total_chars"] += char_count

    lines = ["=== File Tree Analysis ===", ""]
    total_files = 0
    total_lines = 0
    total_chars = 0
    for ext in sorted(ext_stats.keys()):
        info = ext_stats[ext]
        lines.append(f"Extension: {ext}")
        lines.append(f"  Files: {len(info['files'])}")
        lines.append(f"  Total Lines: {info['total_lines']}")
        lines.append(f"  Total Characters: {info['total_chars']}")
        for path, lc, cc in sorted(info["files"]):
            lines.append(f"    {path}: {lc} lines, {cc} chars")
        total_files += len(info["files"])
        total_lines += info["total_lines"]
        total_chars += info["total_chars"]

    lines.append("")
    lines.append(f"Total: {total_files} files, {total_lines} lines, {total_chars} characters")

    return "\n".join(lines)


def compute_sysinfo_expected():
    """Run uname -s, whoami, and echo $SHELL, produce JSON output."""
    # We'll compute this dynamically since it depends on the system
    uname_s = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
    uname_m = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip()
    whoami = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
    hostname = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()

    result = {
        "hostname": hostname,
        "os": uname_s,
        "architecture": uname_m,
        "username": whoami,
    }
    return json.dumps(result, indent=2)


def compute_matrix_expected():
    """Multiply two 3x3 matrices and compute determinant of result."""
    A = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    B = [[9, 8, 7], [6, 5, 4], [3, 2, 1]]

    # Multiply
    C = [[0]*3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            for k in range(3):
                C[i][j] += A[i][k] * B[k][j]

    # Determinant of C
    det = (C[0][0] * (C[1][1]*C[2][2] - C[1][2]*C[2][1])
         - C[0][1] * (C[1][0]*C[2][2] - C[1][2]*C[2][0])
         + C[0][2] * (C[1][0]*C[2][1] - C[1][1]*C[2][0]))

    lines = ["=== Matrix Operations ===", ""]
    lines.append("Matrix A:")
    for row in A:
        lines.append("  " + " ".join(f"{v:4d}" for v in row))
    lines.append("")
    lines.append("Matrix B:")
    for row in B:
        lines.append("  " + " ".join(f"{v:4d}" for v in row))
    lines.append("")
    lines.append("A x B =")
    for row in C:
        lines.append("  " + " ".join(f"{v:4d}" for v in row))
    lines.append("")
    lines.append(f"Determinant: {det}")

    return "\n".join(lines)


# ── Challenge Definitions ──────────────────────────────────────────────────

def get_challenges(fixture_dir: str, port: int):
    """Return challenges with fixture paths and port pre-filled.
    Uses LANG_PLACEHOLDER which gets replaced per-test in run_challenge.
    """
    L = "LANG_PLACEHOLDER"
    return [
        {
            "name": "json_deep_merge",
            "description": "Read two JSON config files, deep merge with rules, output sorted result",
            "prompt": (
                f"Write a complete, runnable {L} program that reads two JSON files:\n"
                f"  - {fixture_dir}/config_a.json\n"
                f"  - {fixture_dir}/config_b.json\n\n"
                "Deep merge them with these rules:\n"
                "  - If both values are objects: merge recursively\n"
                "  - If both values are arrays: concatenate and remove duplicates (preserve order, items from A first, then new items from B)\n"
                "  - If scalar conflict: value from B overrides A\n"
                "  - Keys only in A or only in B are included as-is\n\n"
                "Output the merged JSON with all object keys sorted alphabetically at every level, "
                "pretty-printed with 2-space indentation.\n"
                "Output ONLY the code inside a single code block, no explanations."
            ),
            "expected_output": compute_json_merge_expected(),
        },
        {
            "name": "csv_analysis",
            "description": "Read CSV employee data, compute salary statistics, output formatted report",
            "prompt": (
                f"Write a complete, runnable {L} program that reads the CSV file:\n"
                f"  {fixture_dir}/employees.csv\n\n"
                "The CSV has columns: employee_id, name, department, salary, start_date, rating\n\n"
                "Produce this exact output format:\n"
                "=== Salary Report ===\n\n"
                "Average Salary by Department:\n"
                "  Department: $XX,XXX.XX   (for each dept, sorted alphabetically)\n\n"
                "Highest Paid: Name ($XXX,XXX)\n\n"
                "Departments with >2 employees:\n"
                "  DeptName (N employees)\n\n"
                "Total Budget: $XXX,XXX\n\n"
                "Use comma formatting for numbers (e.g. $95,000 not $95000). "
                "Average salaries should show 2 decimal places. Total budget and highest paid salary should be integers with commas.\n"
                "Output ONLY the code inside a single code block, no explanations."
            ),
            "expected_output": compute_csv_expected(),
        },
        {
            "name": "log_parser",
            "description": "Parse structured log file with regex, extract statistics",
            "prompt": (
                f"Write a complete, runnable {L} program that reads the log file:\n"
                f"  {fixture_dir}/app.log\n\n"
                "Each line has format: [YYYY-MM-DD HH:MM:SS] LEVEL (component) - message\n\n"
                "Parse the file and output:\n"
                "=== Log Analysis ===\n\n"
                "Entries by Level:\n"
                "  LEVEL: count   (sorted alphabetically by level name)\n\n"
                "Component with Most Errors: component_name (N errors)\n\n"
                "Unique Error Messages:\n"
                "  - message   (sorted alphabetically, just the message part after ' - ')\n\n"
                "Busiest Hour: HH:00 (N entries)\n"
                "(use the hour with the most log entries, format as 2-digit hour like 08:00, 10:00)\n\n"
                "Output ONLY the code inside a single code block, no explanations."
            ),
            "expected_output": compute_log_expected(),
        },
        {
            "name": "http_client",
            "description": "Fetch products from HTTP API, filter and aggregate",
            "prompt": (
                f"Write a complete, runnable {L} program that:\n"
                f"1. Makes an HTTP GET request to http://127.0.0.1:{port}/api/products\n"
                "2. Parses the JSON response (array of objects with fields: id, name, price, category, stock)\n"
                "3. Filters to only products where price > 50.00\n"
                "4. Sorts filtered products by name ascending (alphabetical)\n"
                "5. Prints the output in this exact format:\n\n"
                "=== Filtered Products (price > $50.00) ===\n\n"
                "ProductName: $XX.XX   (one per line, price with exactly 2 decimal places)\n\n"
                "Count: N\n"
                "Total: $XXX.XX   (sum of filtered prices, 2 decimal places)\n\n"
                "Do NOT use any external packages/dependencies beyond the standard library. "
                "Output ONLY the code inside a single code block, no explanations."
            ),
            "expected_output": compute_http_expected(),
        },
        {
            "name": "dijkstra_graph",
            "description": "Read weighted graph from JSON, implement Dijkstra's shortest path",
            "prompt": (
                f"Write a complete, runnable {L} program that reads a weighted graph from:\n"
                f"  {fixture_dir}/graph.json\n\n"
                'The JSON has structure: {"nodes": ["A","B",...], "edges": [{"from":"X","to":"Y","weight":N}, ...]}\n'
                "The graph is UNDIRECTED (each edge works both ways).\n\n"
                "Implement Dijkstra's algorithm to find the shortest path distance from node \"A\" "
                "to every other node.\n\n"
                "Output in this exact format:\n"
                "=== Shortest Paths from A ===\n\n"
                "A -> X: distance   (one line per node, sorted alphabetically, including A -> A: 0)\n\n"
                "Do not use any graph library. Implement the algorithm yourself.\n"
                "Output ONLY the code inside a single code block, no explanations."
            ),
            "expected_output": compute_graph_expected(),
        },
        {
            "name": "file_tree_analysis",
            "description": "Walk directory tree, compute per-file and per-extension statistics",
            "prompt": (
                f"Write a complete, runnable {L} program that recursively walks the directory:\n"
                f"  {fixture_dir}/project_tree\n\n"
                "For each file found, compute its line count and character count (total bytes in the file).\n"
                "Group files by their extension (e.g., '.py', '.md').\n\n"
                "Output in this exact format:\n"
                "=== File Tree Analysis ===\n\n"
                "Extension: .ext\n"
                "  Files: N\n"
                "  Total Lines: N\n"
                "  Total Characters: N\n"
                "    relative/path: N lines, N chars\n"
                "(repeat for each extension, sorted by extension alphabetically)\n"
                "(within each extension, list files sorted by relative path)\n"
                "(relative paths should be relative to the project_tree directory, using / separators)\n"
                "(do NOT put a blank line between extension groups — list them consecutively; "
                "the only blank lines are the one right after the title and the one right before the final Total line)\n"
                "(count each file exactly once; do not follow symlinks or revisit directories)\n\n"
                "Total: N files, N lines, N characters\n\n"
                "Output ONLY the code inside a single code block, no explanations."
            ),
            "expected_output": compute_filetree_expected(),
        },
        {
            "name": "system_info",
            "description": "Execute system commands, parse output, produce structured JSON",
            "prompt": (
                f"Write a complete, runnable {L} program that:\n"
                "1. Runs the command 'uname -s' and captures the output (the OS name)\n"
                "2. Runs the command 'uname -m' and captures the output (the architecture)\n"
                "3. Runs the command 'whoami' and captures the output (the username)\n"
                "4. Runs the command 'hostname' and captures the output\n\n"
                "Then output a JSON object (pretty-printed with 2-space indent) with these exact keys:\n"
                '{"hostname": "...", "os": "...", "architecture": "...", "username": "..."}\n\n'
                "Keys must be in this exact order: hostname, os, architecture, username.\n"
                "Strip any trailing newlines/whitespace from command output.\n"
                "Output ONLY the code inside a single code block, no explanations."
            ),
            "expected_output": compute_sysinfo_expected(),
        },
        {
            "name": "matrix_ops",
            "description": "Multiply two 3x3 matrices and compute determinant without libraries",
            "prompt": (
                f"Write a complete, runnable {L} program that:\n"
                "1. Defines Matrix A = [[1,2,3],[4,5,6],[7,8,9]]\n"
                "2. Defines Matrix B = [[9,8,7],[6,5,4],[3,2,1]]\n"
                "3. Computes C = A x B (standard matrix multiplication)\n"
                "4. Computes the determinant of C\n\n"
                "Output in this exact format:\n"
                "=== Matrix Operations ===\n\n"
                "Matrix A:\n"
                "  (each row as space-separated values, each value right-justified in 4 chars)\n\n"
                "Matrix B:\n"
                "  (same format)\n\n"
                "A x B =\n"
                "  (same format)\n\n"
                "Determinant: N\n\n"
                "For the matrix display, print each row as: a 2-space indent, then each value "
                "right-justified in a 4-character-wide field, with the fields separated by a single space. "
                "This is exactly Python's \"  \" + \" \".join(f\"{v:4d}\" for v in row). "
                "For the row [1, 2, 3] this produces '     1    2    3' "
                "(2-space indent, then '   1', a space, '   2', a space, '   3' — i.e. 4 spaces between values).\n"
                "Do not use any matrix/math library. Implement multiplication and determinant yourself.\n"
                "Output ONLY the code inside a single code block, no explanations."
            ),
            "expected_output": compute_matrix_expected(),
        },
    ]


# ── Language Config ────────────────────────────────────────────────────────
LANGUAGES = {
    "python": {
        "extension": ".py",
        "name": "Python",
        "run": lambda path: ["python3", path],
        "compile": None,
    },
    "java": {
        "extension": ".java",
        "name": "Java",
        "compile": lambda path: [f"{JAVA_HOME_BIN}/javac", path],
        "run": lambda path: [f"{JAVA_HOME_BIN}/java", "-cp", str(Path(path).parent), "Main"],
        "filename": "Main.java",
    },
    "swift": {
        "extension": ".swift",
        "name": "Swift",
        "run": lambda path: ["swift", path],
        "compile": None,
    },
    "typescript": {
        "extension": ".ts",
        "name": "TypeScript",
        "run": lambda path: ["npx", "tsx", path],
        "compile": None,
    },
    "c": {
        "extension": ".c",
        "name": "C",
        "compile": lambda path: ["cc", path, "-o", str(Path(path).parent / "main_c"), "-lm"],
        "run": lambda path: [str(Path(path).parent / "main_c")],
        "filename": "main.c",
    },
    "rust": {
        "extension": ".rs",
        "name": "Rust",
        "compile": lambda path: ["rustc", "-O", "-A", "warnings", path, "-o", str(Path(path).parent / "main_rs")],
        "run": lambda path: [str(Path(path).parent / "main_rs")],
        "filename": "main.rs",
    },
}


# ── Code Extraction ────────────────────────────────────────────────────────
def extract_code(text: str, lang: str) -> str:
    """Extract code from model output, handling markdown code blocks."""
    lang_aliases = {
        "python": ["python", "py", "python3"],
        "java": ["java"],
        "swift": ["swift"],
        "typescript": ["typescript", "ts", "tsx"],
        "c": ["c"],
        "rust": ["rust", "rs"],
    }
    aliases = lang_aliases.get(lang, [lang])

    # Models frequently split a solution across SEVERAL fenced blocks (e.g. a
    # struct/imports block then a main block). Taking only the first block yields
    # a fragment that fails to compile ("main not found" / "class expected at
    # line 1"), so concatenate ALL same-language blocks in order.
    for alias in aliases:
        blocks = re.findall(rf"```{alias}\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if blocks:
            return "\n\n".join(b.strip() for b in blocks).strip()

    # Fallback: concatenate every fenced block (tagged or not), in order.
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    if blocks:
        return "\n\n".join(b.strip() for b in blocks).strip()

    return text.strip()


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks from output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ── Code Runner ────────────────────────────────────────────────────────────
def _run(cmd, timeout, env):
    """subprocess.run, but a timeout kills the whole process group: `npx tsx` and `java` start grandchildren that
    keep the pipes open, and plain run() then waits on them forever."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, 9)
        except ProcessLookupError:
            pass
        p.communicate()
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def run_code(code: str, lang: str, extra_files: dict = None) -> tuple[bool, str]:
    """Compile (if needed) and run code. Returns (success, output_or_error)."""
    lang_info = LANGUAGES[lang]

    with tempfile.TemporaryDirectory() as tmpdir:
        filename = lang_info.get("filename", f"main{lang_info['extension']}")
        filepath = os.path.join(tmpdir, filename)

        with open(filepath, "w") as f:
            f.write(code)

        if extra_files:
            for name, content in extra_files.items():
                with open(os.path.join(tmpdir, name), "w") as f:
                    f.write(content)

        env = os.environ.copy()
        env["PATH"] = f"{JAVA_HOME_BIN}:{env.get('PATH', '')}"

        if lang_info.get("compile"):
            try:
                result = _run(lang_info["compile"](filepath), TIMEOUT_SECONDS, env)
                if result.returncode != 0:
                    return False, f"COMPILATION ERROR:\n{result.stderr}"
            except subprocess.TimeoutExpired:
                return False, "COMPILATION TIMEOUT"

        try:
            result = _run(lang_info["run"](filepath), TIMEOUT_SECONDS, env)
            if result.returncode != 0:
                return False, f"RUNTIME ERROR:\n{result.stderr}"
            return True, result.stdout.rstrip()
        except subprocess.TimeoutExpired:
            return False, "RUNTIME TIMEOUT"


def check_output(actual: str, expected: str) -> bool:
    """Compare outputs, normalizing trailing whitespace per line."""
    actual_lines = [line.rstrip() for line in actual.strip().splitlines()]
    expected_lines = [line.rstrip() for line in expected.strip().splitlines()]
    return actual_lines == expected_lines


# ── Language Verification ──────────────────────────────────────────────────
LANG_TESTS = {
    "python": 'print("LANG_OK")',
    "java": 'public class Main { public static void main(String[] args) { System.out.println("LANG_OK"); } }',
    "swift": 'print("LANG_OK")',
    "typescript": 'console.log("LANG_OK");',
    "c": '#include <stdio.h>\nint main(void) { printf("LANG_OK\\n"); return 0; }',
    "rust": 'fn main() { println!("LANG_OK"); }',
}

def verify_languages() -> dict:
    """Smoke-test every toolchain. Returns {lang: "" if OK else the error}."""
    out = {}
    for lang, code in LANG_TESTS.items():
        ok, output = run_code(code, lang)
        out[lang] = "" if ok and "LANG_OK" in output else (output[:300] or "failed")
    return out


def build_prompt(challenge: dict, lang: str) -> str:
    """Substitute the language name and append the requirements the harness
    imposes (so they aren't left to chance)."""
    prompt = challenge["prompt"].replace("LANG_PLACEHOLDER", LANGUAGES[lang]["name"])
    # Targeted formatting guidance. Blank lines being dropped/added vs the spec is
    # the top source of spurious failures, but the title-blank note only applies to
    # challenges whose output starts with a '=== ... ===' header (json_deep_merge and
    # system_info emit raw JSON with no title — adding it there makes the model invent
    # a header). Keep the rest minimal to avoid destabilising already-passing cases.
    prompt += ("\n\nYour program is saved as a single file, compiled (if the language requires it), "
               "and executed; it MUST compile with no errors and run to completion, writing the "
               "specified output to standard output (stdout) and nothing else. "
               "Put the ENTIRE program in ONE single code block — do not split the code across "
               "multiple code blocks. Output ONLY what the format specifies — no extra heading, "
               "banner, or commentary before or after it — and use ONLY the standard library "
               "(no third-party packages/modules/crates).")
    if challenge["expected_output"].lstrip().startswith("==="):
        prompt += (" The output begins with a '=== ... ===' title line, IMMEDIATELY followed by one "
                   "blank line, then the content; reproduce that blank line and every other blank "
                   "line shown in the format exactly (do not drop or add blank lines).")
    # Per-language structure the harness imposes (entry point / compiler / target),
    # so the model isn't left to guess how its file will be built and run. Natively
    # compiled languages (C, Rust) are told the target architecture — it affects
    # int/pointer sizes, available POSIX APIs, and linker limits (e.g. huge global
    # arrays overflow the arm64 small-code-model addressing).
    entry = {
        "java": " IMPORTANT (Java): the file is saved as Main.java and class Main is run, so provide "
                "exactly one public class named Main with `public static void main(String[] args)`.",
        "c":    f" IMPORTANT (C): provide a single complete C source file with all necessary #include "
                f"directives and an `int main(void)`; it is compiled with `cc <file> -lm` and the binary "
                f"executed. Target: {TARGET_PLATFORM} (Apple clang, LP64 — long/pointer are 64-bit). "
                f"Allocate large buffers dynamically (malloc); avoid multi-megabyte fixed-size global arrays.",
        "rust": f" IMPORTANT (Rust): provide a single complete file with a `fn main()`; it is compiled "
                f"with `rustc` (standard library only) and the binary executed. Target: {TARGET_PLATFORM} "
                f"(64-bit; usize is 64-bit).",
        "swift": " (Swift): the file is run directly with `swift`, so top-level statements execute — "
                 "no special entry point is required; just print the output to stdout.",
    }
    if lang in entry:
        prompt += entry[lang]
    return prompt

