use std::io::Write;

struct Db {
    data: Vec<u8>,
    page_size: usize,
    usable: usize,
}

fn be16(p: &[u8]) -> usize {
    ((p[0] as usize) << 8) | p[1] as usize
}
fn be32(p: &[u8]) -> u32 {
    ((p[0] as u32) << 24) | ((p[1] as u32) << 16) | ((p[2] as u32) << 8) | p[3] as u32
}

fn varint(p: &[u8]) -> (u64, usize) {
    let mut v: u64 = 0;
    for i in 0..8 {
        v = (v << 7) | (p[i] & 0x7f) as u64;
        if p[i] & 0x80 == 0 {
            return (v, i + 1);
        }
    }
    v = (v << 8) | p[8] as u64;
    (v, 9)
}

impl Db {
    fn page(&self, n: u32) -> &[u8] {
        let off = (n as usize - 1) * self.page_size;
        &self.data[off..off + self.page_size]
    }

    fn walk(&self, pg: u32, rows: &mut Vec<(i64, Vec<u8>)>, depth: usize) {
        if depth > 64 {
            panic!("too deep");
        }
        let p = self.page(pg);
        let h = if pg == 1 { 100 } else { 0 };
        let typ = p[h];
        let ncell = be16(&p[h + 3..]);
        if typ == 0x05 {
            for i in 0..ncell {
                let off = be16(&p[h + 12 + 2 * i..]);
                self.walk(be32(&p[off..]), rows, depth + 1);
            }
            self.walk(be32(&p[h + 8..]), rows, depth + 1);
        } else if typ == 0x0d {
            let u = self.usable;
            for i in 0..ncell {
                let off = be16(&p[h + 8 + 2 * i..]);
                let (plen, l1) = varint(&p[off..]);
                let (rowid, l2) = varint(&p[off + l1..]);
                let start = off + l1 + l2;
                let plen = plen as usize;
                let x = u - 35;
                let mut out = Vec::with_capacity(plen);
                if plen <= x {
                    out.extend_from_slice(&p[start..start + plen]);
                } else {
                    let m = ((u - 12) * 32 / 255) - 23;
                    let k = m + ((plen - m) % (u - 4));
                    let local = if k <= x { k } else { m };
                    out.extend_from_slice(&p[start..start + local]);
                    let mut ov = be32(&p[start + local..]);
                    while out.len() < plen && ov != 0 {
                        let op = self.page(ov);
                        let take = std::cmp::min(plen - out.len(), u - 4);
                        out.extend_from_slice(&op[4..4 + take]);
                        ov = be32(op);
                    }
                }
                rows.push((rowid as i64, out));
            }
        } else {
            panic!("unexpected page type {} at {}", typ, pg);
        }
    }
}

enum Val<'a> {
    Null,
    Int(i64),
    Real(f64),
    Text(&'a [u8]),
    Blob(&'a [u8]),
}

fn decode(p: &[u8]) -> Vec<Val> {
    let (hlen, l) = varint(p);
    let hlen = hlen as usize;
    let mut hp = l;
    let mut dp = hlen;
    let mut v = Vec::new();
    while hp < hlen {
        let (st, l) = varint(&p[hp..]);
        hp += l;
        let val = match st {
            0 => Val::Null,
            1..=6 => {
                let n = [0usize, 1, 2, 3, 4, 6, 8][st as usize];
                let mut u: u64 = 0;
                for i in 0..n {
                    u = (u << 8) | p[dp + i] as u64;
                }
                if n < 8 && (u >> (n * 8 - 1)) & 1 == 1 {
                    u |= !0u64 << (n * 8);
                }
                dp += n;
                Val::Int(u as i64)
            }
            7 => {
                let mut u: u64 = 0;
                for i in 0..8 {
                    u = (u << 8) | p[dp + i] as u64;
                }
                dp += 8;
                Val::Real(f64::from_bits(u))
            }
            8 => Val::Int(0),
            9 => Val::Int(1),
            _ if st >= 12 => {
                let n = ((st - 12) / 2) as usize;
                let s = &p[dp..dp + n];
                dp += n;
                if st & 1 == 1 {
                    Val::Text(s)
                } else {
                    Val::Blob(s)
                }
            }
            _ => Val::Null,
        };
        v.push(val);
    }
    v
}

fn crc32(s: &[u8], table: &[u32; 256]) -> u32 {
    let mut c = 0xffff_ffffu32;
    for &b in s {
        c = table[((c ^ b as u32) & 0xff) as usize] ^ (c >> 8);
    }
    c ^ 0xffff_ffff
}

fn fmt_real(d: f64) -> String {
    let mut s = format!("{:.1100}", d);
    while s.ends_with('0') {
        s.pop();
    }
    if s.ends_with('.') {
        s.push('0');
    }
    s
}

fn fmt_val(out: &mut Vec<u8>, v: &Val, table: &[u32; 256]) {
    match v {
        Val::Null => out.extend_from_slice(b"NULL"),
        Val::Int(i) => out.extend_from_slice(i.to_string().as_bytes()),
        Val::Real(d) => out.extend_from_slice(fmt_real(*d).as_bytes()),
        Val::Text(s) => {
            if s.len() > 100 {
                out.extend_from_slice(format!("<TEXT len={} crc32={:08x}>", s.len(), crc32(s, table)).as_bytes());
            } else {
                out.push(b'"');
                for &c in s.iter() {
                    match c {
                        b'\\' => out.extend_from_slice(b"\\\\"),
                        b'"' => out.extend_from_slice(b"\\\""),
                        b'\n' => out.extend_from_slice(b"\\n"),
                        b'\r' => out.extend_from_slice(b"\\r"),
                        b'\t' => out.extend_from_slice(b"\\t"),
                        c if c < 0x20 || c == 0x7f => out.extend_from_slice(format!("\\x{:02X}", c).as_bytes()),
                        c => out.push(c),
                    }
                }
                out.push(b'"');
            }
        }
        Val::Blob(s) => {
            if s.len() > 100 {
                out.extend_from_slice(format!("<BLOB len={} crc32={:08x}>", s.len(), crc32(s, table)).as_bytes());
            } else {
                out.extend_from_slice(b"X'");
                for &c in s.iter() {
                    out.extend_from_slice(format!("{:02X}", c).as_bytes());
                }
                out.push(b'\'');
            }
        }
    }
}

fn is_id(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_' || c == b'$' || c >= 0x80
}

// returns (name, is_rowid_alias)
fn parse_cols(sql: &[u8]) -> Vec<(Vec<u8>, bool)> {
    let n = sql.len();
    let mut i = 0;
    while i < n && sql[i] != b'(' {
        if sql[i] == b'"' {
            i += 1;
            while i < n && sql[i] != b'"' {
                i += 1;
            }
        }
        i += 1;
    }
    let start = i + 1;
    let mut depth = 1;
    let mut end = n;
    let mut j = start;
    while j < n {
        let c = sql[j];
        if c == b'"' {
            j += 1;
            while j < n && sql[j] != b'"' {
                j += 1;
            }
        } else if c == b'(' {
            depth += 1;
        } else if c == b')' {
            depth -= 1;
            if depth == 0 {
                end = j;
                break;
            }
        }
        j += 1;
    }
    // split defs
    let mut defs: Vec<&[u8]> = Vec::new();
    let mut ds = start;
    let mut depth = 0;
    let mut j = start;
    while j <= end {
        let c = if j == end { b',' } else { sql[j] };
        if c == b'"' && j < end {
            j += 1;
            while j < end && sql[j] != b'"' {
                j += 1;
            }
        } else if c == b'(' {
            depth += 1;
        } else if c == b')' {
            depth -= 1;
        } else if c == b',' && depth == 0 {
            defs.push(&sql[ds..j]);
            ds = j + 1;
        }
        j += 1;
    }
    let kws = ["CONSTRAINT", "PRIMARY", "NOT", "NULL", "UNIQUE", "CHECK", "DEFAULT", "COLLATE", "REFERENCES", "GENERATED", "AS"];
    let mut cols = Vec::new();
    for d in defs {
        let mut toks: Vec<Vec<u8>> = Vec::new();
        let mut k = 0;
        while k < d.len() {
            let c = d[k];
            if c.is_ascii_whitespace() {
                k += 1;
            } else if c == b'"' {
                let s = k;
                k += 1;
                while k < d.len() && d[k] != b'"' {
                    k += 1;
                }
                k += 1;
                toks.push(d[s..std::cmp::min(k, d.len())].to_vec());
            } else if is_id(c) {
                let s = k;
                while k < d.len() && is_id(d[k]) {
                    k += 1;
                }
                toks.push(d[s..k].to_vec());
            } else {
                toks.push(vec![c]);
                k += 1;
            }
        }
        if toks.is_empty() {
            continue;
        }
        let name = if toks[0][0] == b'"' {
            toks[0][1..toks[0].len() - 1].to_vec()
        } else {
            toks[0].clone()
        };
        let up: Vec<String> = toks.iter().map(|t| String::from_utf8_lossy(t).to_uppercase()).collect();
        let mut t = 1;
        let mut words = 0;
        let mut is_int = false;
        while t < toks.len() {
            let c = toks[t][0];
            if !(is_id(c) || c == b'"') {
                break;
            }
            if kws.contains(&up[t].as_str()) {
                break;
            }
            words += 1;
            if words == 1 && up[t] == "INTEGER" {
                is_int = true;
            }
            t += 1;
        }
        let paren = t < toks.len() && toks[t][0] == b'(' && words > 0;
        let mut pk = false;
        for q in 0..up.len().saturating_sub(1) {
            if up[q] == "PRIMARY" && up[q + 1] == "KEY" {
                pk = true;
            }
        }
        cols.push((name, words == 1 && is_int && !paren && pk));
    }
    cols
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let path = format!("{}/db.sqlite", args[1]);
    let data = std::fs::read(&path).expect("read db");
    let mut ps = be16(&data[16..]);
    if ps == 1 {
        ps = 65536;
    }
    let usable = ps - data[20] as usize;
    let db = Db { data, page_size: ps, usable };

    let mut table = [0u32; 256];
    for i in 0..256u32 {
        let mut c = i;
        for _ in 0..8 {
            c = if c & 1 == 1 { 0xedb88320 ^ (c >> 1) } else { c >> 1 };
        }
        table[i as usize] = c;
    }

    let mut schema = Vec::new();
    db.walk(1, &mut schema, 0);
    let mut tabs: Vec<(Vec<u8>, u32, Vec<u8>)> = Vec::new();
    for (_, payload) in &schema {
        let v = decode(payload);
        if v.len() < 5 {
            continue;
        }
        let is_table = matches!(v[0], Val::Text(s) if s == b"table");
        if !is_table {
            continue;
        }
        let name = match v[1] {
            Val::Text(s) => s.to_vec(),
            _ => continue,
        };
        if name.starts_with(b"sqlite_") {
            continue;
        }
        let root = match v[3] {
            Val::Int(i) => i as u32,
            _ => 0,
        };
        let sql = match v[4] {
            Val::Text(s) => s.to_vec(),
            _ => Vec::new(),
        };
        tabs.push((name, root, sql));
    }
    tabs.sort_by(|a, b| a.0.cmp(&b.0));

    let mut out: Vec<u8> = Vec::new();
    for (ti, (name, root, sql)) in tabs.iter().enumerate() {
        let cols = parse_cols(sql);
        let mut rows = Vec::new();
        if *root != 0 {
            db.walk(*root, &mut rows, 0);
        }
        if ti > 0 {
            out.push(b'\n');
        }
        out.extend_from_slice(b"TABLE ");
        out.extend_from_slice(name);
        out.extend_from_slice(format!(" rows={}\nCOLUMNS ", rows.len()).as_bytes());
        for (i, c) in cols.iter().enumerate() {
            if i > 0 {
                out.extend_from_slice(b", ");
            }
            out.extend_from_slice(&c.0);
        }
        out.push(b'\n');
        for (rowid, payload) in &rows {
            let v = decode(payload);
            out.extend_from_slice(format!("{}: ", rowid).as_bytes());
            for (i, c) in cols.iter().enumerate() {
                if i > 0 {
                    out.extend_from_slice(b", ");
                }
                if c.1 {
                    out.extend_from_slice(rowid.to_string().as_bytes());
                } else if i < v.len() {
                    fmt_val(&mut out, &v[i], &table);
                } else {
                    out.extend_from_slice(b"NULL");
                }
            }
            out.push(b'\n');
        }
    }
    std::io::stdout().write_all(&out).unwrap();
}
