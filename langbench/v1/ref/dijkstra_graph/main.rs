use std::fs;

const INF: i64 = 1_000_000_000;

// Extract the next double-quoted string starting from byte index `start`.
// Returns (string, index just past closing quote) or None.
fn next_string(bytes: &[u8], start: usize) -> Option<(String, usize)> {
    let mut i = start;
    while i < bytes.len() && bytes[i] != b'"' {
        i += 1;
    }
    if i >= bytes.len() {
        return None;
    }
    i += 1; // past opening quote
    let begin = i;
    while i < bytes.len() && bytes[i] != b'"' {
        i += 1;
    }
    if i >= bytes.len() {
        return None;
    }
    let s = String::from_utf8_lossy(&bytes[begin..i]).into_owned();
    Some((s, i + 1))
}

fn find_from(hay: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if needle.is_empty() || from > hay.len() {
        return None;
    }
    let end = hay.len();
    let mut i = from;
    while i + needle.len() <= end {
        if &hay[i..i + needle.len()] == needle {
            return Some(i);
        }
        i += 1;
    }
    None
}

fn main() {
    let content = fs::read_to_string("__FIXTURE_DIR__/graph.json")
        .expect("failed to read graph.json");
    let bytes = content.as_bytes();

    let nodes_key = find_from(bytes, b"\"nodes\"", 0).expect("no nodes key");
    let edges_key = find_from(bytes, b"\"edges\"", 0).expect("no edges key");

    // Parse node names between nodes_key and edges_key (nodes appears first).
    let mut nodes: Vec<String> = Vec::new();
    // skip to '[' after nodes_key
    let mut i = nodes_key + "\"nodes\"".len();
    while i < bytes.len() && bytes[i] != b'[' {
        i += 1;
    }
    if i < bytes.len() {
        i += 1;
    }
    while i < edges_key {
        // find next quote before edges_key
        let mut q = i;
        while q < edges_key && bytes[q] != b'"' {
            q += 1;
        }
        if q >= edges_key {
            break;
        }
        match next_string(bytes, q) {
            Some((s, ni)) => {
                nodes.push(s);
                i = ni;
            }
            None => break,
        }
    }

    let node_index = |name: &str, nodes: &[String]| -> i64 {
        for (idx, n) in nodes.iter().enumerate() {
            if n == name {
                return idx as i64;
            }
        }
        -1
    };

    // Parse edges.
    let mut edges: Vec<(usize, usize, i64)> = Vec::new();
    let mut p = edges_key + "\"edges\"".len();
    while p < bytes.len() && bytes[p] != b'[' {
        p += 1;
    }
    if p < bytes.len() {
        p += 1;
    }
    loop {
        let fk = match find_from(bytes, b"\"from\"", p) {
            Some(x) => x,
            None => break,
        };
        let (from_name, fp) = match next_string(bytes, fk + "\"from\"".len()) {
            Some(x) => x,
            None => break,
        };
        let tk = match find_from(bytes, b"\"to\"", fp) {
            Some(x) => x,
            None => break,
        };
        let (to_name, tp) = match next_string(bytes, tk + "\"to\"".len()) {
            Some(x) => x,
            None => break,
        };
        let wk = match find_from(bytes, b"\"weight\"", tp) {
            Some(x) => x,
            None => break,
        };
        // skip to ':' then number
        let mut wp = wk + "\"weight\"".len();
        while wp < bytes.len() && bytes[wp] != b':' {
            wp += 1;
        }
        if wp < bytes.len() {
            wp += 1;
        }
        while wp < bytes.len()
            && (bytes[wp] == b' '
                || bytes[wp] == b'\t'
                || bytes[wp] == b'\n'
                || bytes[wp] == b'\r')
        {
            wp += 1;
        }
        let num_begin = wp;
        if wp < bytes.len() && (bytes[wp] == b'-' || bytes[wp] == b'+') {
            wp += 1;
        }
        while wp < bytes.len() && bytes[wp].is_ascii_digit() {
            wp += 1;
        }
        let weight: i64 = content[num_begin..wp].parse().unwrap_or(0);

        let fi = node_index(&from_name, &nodes);
        let ti = node_index(&to_name, &nodes);
        if fi >= 0 && ti >= 0 {
            edges.push((fi as usize, ti as usize, weight));
        }
        p = wp;
    }

    let n = nodes.len();
    let mut dist = vec![INF; n];
    let mut done = vec![false; n];

    let src = node_index("A", &nodes);
    if src < 0 {
        eprintln!("no node A");
        std::process::exit(1);
    }
    let src = src as usize;
    dist[src] = 0;

    for _ in 0..n {
        let mut u: i64 = -1;
        let mut best = INF;
        for i in 0..n {
            if !done[i] && dist[i] < best {
                best = dist[i];
                u = i as i64;
            }
        }
        if u < 0 {
            break;
        }
        let u = u as usize;
        done[u] = true;
        for &(a, b, w) in &edges {
            if a == u && dist[u] + w < dist[b] {
                dist[b] = dist[u] + w;
            } else if b == u && dist[u] + w < dist[a] {
                dist[a] = dist[u] + w;
            }
        }
    }

    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by(|&a, &b| nodes[a].cmp(&nodes[b]));

    let mut out = String::new();
    out.push_str("=== Shortest Paths from A ===\n");
    out.push('\n');
    for &idx in &order {
        out.push_str(&format!("A -> {}: {}\n", nodes[idx], dist[idx]));
    }
    print!("{}", out);
}
