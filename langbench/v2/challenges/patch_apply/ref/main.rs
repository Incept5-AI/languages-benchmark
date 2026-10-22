use std::fs;
use std::io::Write;
use std::path::Path;

struct Hunk {
    a: i64,
    b: i64,
    d: i64,
    malformed: bool,
    old_l: Vec<Vec<u8>>,
    new_l: Vec<Vec<u8>>,
}

struct Section {
    path: String,
    kind: &'static str,
    hunks: Vec<Hunk>,
}

fn split_lines(data: &[u8]) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    let mut start = 0;
    for i in 0..data.len() {
        if data[i] == b'\n' {
            out.push(data[start..i + 1].to_vec());
            start = i + 1;
        }
    }
    if start < data.len() {
        out.push(data[start..].to_vec());
    }
    out
}

fn trim_eol(l: &[u8]) -> &[u8] {
    let mut n = l.len();
    while n > 0 && (l[n - 1] == b'\n' || l[n - 1] == b'\r') {
        n -= 1;
    }
    &l[..n]
}

fn parse_range(s: &str) -> Option<(i64, i64)> {
    if let Some((x, y)) = s.split_once(',') {
        Some((x.parse().ok()?, y.parse().ok()?))
    } else {
        Some((s.parse().ok()?, 1))
    }
}

fn parse_header(l: &[u8]) -> Option<(i64, i64, i64, i64)> {
    let s = String::from_utf8_lossy(trim_eol(l)).to_string();
    let rest = s.strip_prefix("@@ -")?;
    let sp = rest.find(' ')?;
    let (a, b) = parse_range(&rest[..sp])?;
    let rest = rest[sp + 1..].strip_prefix('+')?;
    let sp2 = rest.find(' ')?;
    let (c, d) = parse_range(&rest[..sp2])?;
    let rest = &rest[sp2 + 1..];
    if !rest.starts_with("@@") {
        return None;
    }
    Some((a, b, c, d))
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let root = Path::new(&args[1]);
    let diff = fs::read(root.join("changes.diff")).unwrap();
    let lines = split_lines(&diff);
    let mut sections: Vec<Section> = Vec::new();
    let mut in_hunk = false;
    for i in 0..lines.len() {
        let l = &lines[i];
        if l.starts_with(b"diff --git ") {
            let s = String::from_utf8_lossy(trim_eol(l)).to_string();
            let parts: Vec<&str> = s.split(' ').collect();
            let mut p = parts.get(2).copied().unwrap_or("").to_string();
            if p.starts_with("a/") {
                p = p[2..].to_string();
            }
            sections.push(Section { path: p, kind: "modify", hunks: Vec::new() });
            in_hunk = false;
            continue;
        }
        let cur = match sections.last_mut() {
            Some(c) => c,
            None => continue,
        };
        if l.starts_with(b"@@ ") {
            let mut h = Hunk { a: 0, b: 0, d: 0, malformed: false, old_l: Vec::new(), new_l: Vec::new() };
            match parse_header(l) {
                Some((a, b, _c, d)) => {
                    h.a = a;
                    h.b = b;
                    h.d = d;
                }
                None => h.malformed = true,
            }
            cur.hunks.push(h);
            in_hunk = true;
            continue;
        }
        if !in_hunk {
            if l.starts_with(b"new file mode") {
                cur.kind = "create";
            } else if l.starts_with(b"deleted file mode") {
                cur.kind = "delete";
            }
            continue;
        }
        if l.is_empty() {
            continue;
        }
        let h = cur.hunks.last_mut().unwrap();
        let mk = l[0];
        if mk == b'\\' {
            continue;
        }
        let mut content = l[1..].to_vec();
        let no_nl = i + 1 < lines.len() && !lines[i + 1].is_empty() && lines[i + 1][0] == b'\\';
        if no_nl && content.last() == Some(&b'\n') {
            content.pop();
        }
        match mk {
            b' ' => {
                h.old_l.push(content.clone());
                h.new_l.push(content);
            }
            b'-' => h.old_l.push(content),
            b'+' => h.new_l.push(content),
            b'\n' => {
                h.old_l.push(vec![b'\n']);
                h.new_l.push(vec![b'\n']);
            }
            _ => {}
        }
    }
    for s in sections.iter_mut() {
        for h in s.hunks.iter_mut() {
            if h.old_l.len() as i64 != h.b || h.new_l.len() as i64 != h.d {
                h.malformed = true;
            }
        }
    }
    sections.sort_by(|x, y| x.path.as_bytes().cmp(y.path.as_bytes()));

    let mut table = [0u32; 256];
    for n in 0..256u32 {
        let mut c = n;
        for _ in 0..8 {
            c = if c & 1 != 0 { 0xEDB88320 ^ (c >> 1) } else { c >> 1 };
        }
        table[n as usize] = c;
    }

    let mut out = String::new();
    for s in &sections {
        out.push_str(&format!("{}: {}\n", s.path, s.kind));
        let fp = root.join("tree").join(&s.path);
        let exists = fp.is_file();
        let mut content: Vec<Vec<u8>>;
        if s.kind == "create" {
            if exists {
                out.push_str("  error: already exists\n");
                continue;
            }
            content = Vec::new();
        } else {
            if !exists {
                out.push_str("  error: not found\n");
                continue;
            }
            content = split_lines(&fs::read(&fp).unwrap());
        }
        let mut delta: i64 = 0;
        let mut all_applied = true;
        for (idx, h) in s.hunks.iter().enumerate() {
            let n = idx + 1;
            if h.malformed {
                out.push_str(&format!("  hunk {}: malformed\n", n));
                all_applied = false;
                continue;
            }
            let pp = h.a + delta;
            if h.b == 0 {
                if pp >= 0 && pp <= content.len() as i64 {
                    let at = pp as usize;
                    content.splice(at..at, h.new_l.iter().cloned());
                    out.push_str(&format!("  hunk {}: applied at {} (offset 0)\n", n, pp));
                    delta += h.d - h.b;
                } else {
                    out.push_str(&format!("  hunk {}: rejected\n", n));
                    all_applied = false;
                }
                continue;
            }
            let max_p = content.len() as i64 - h.b + 1;
            let mut found: i64 = -1;
            if max_p >= 1 {
                let lim = std::cmp::max((pp - 1).abs(), (pp - max_p).abs()) + 1;
                let mut k = 0i64;
                while k <= lim && found < 0 {
                    let cands: Vec<i64> = if k == 0 { vec![pp] } else { vec![pp - k, pp + k] };
                    for p in cands {
                        if p < 1 || p > max_p {
                            continue;
                        }
                        let base = (p - 1) as usize;
                        let ok = (0..h.b as usize).all(|j| content[base + j] == h.old_l[j]);
                        if ok {
                            found = p;
                            break;
                        }
                    }
                    k += 1;
                }
            }
            if found < 0 {
                out.push_str(&format!("  hunk {}: rejected\n", n));
                all_applied = false;
                continue;
            }
            let base = (found - 1) as usize;
            content.splice(base..base + h.b as usize, h.new_l.iter().cloned());
            let off = found - pp;
            let os = if off > 0 { format!("+{}", off) } else { format!("{}", off) };
            out.push_str(&format!("  hunk {}: applied at {} (offset {})\n", n, found, os));
            delta += h.d - h.b;
        }
        if s.kind == "delete" && all_applied && content.is_empty() {
            out.push_str("  result: deleted\n");
            continue;
        }
        let mut c: u32 = 0xFFFFFFFF;
        for l in &content {
            for &b in l {
                c = table[((c ^ b as u32) & 0xFF) as usize] ^ (c >> 8);
            }
        }
        c ^= 0xFFFFFFFF;
        out.push_str(&format!("  result: {} lines, crc32 {:08x}\n", content.len(), c));
    }
    let stdout = std::io::stdout();
    let mut lock = stdout.lock();
    lock.write_all(out.as_bytes()).unwrap();
    lock.flush().unwrap();
}
