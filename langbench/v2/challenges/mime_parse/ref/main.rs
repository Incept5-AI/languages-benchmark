use std::collections::HashMap;
use std::fs;
use std::io::Write;

fn is_ws(c: u8) -> bool {
    c == b' ' || c == b'\t'
}

fn trim_ws(s: &str) -> &str {
    s.trim_matches(|c: char| c == ' ' || c == '\t')
}

fn find(h: &[u8], n: &[u8], from: usize) -> Option<usize> {
    if n.is_empty() || h.len() < n.len() {
        return None;
    }
    let mut i = from;
    while i + n.len() <= h.len() {
        if &h[i..i + n.len()] == n {
            return Some(i);
        }
        i += 1;
    }
    None
}

fn hexv(c: u8) -> Option<u8> {
    match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        b'A'..=b'F' => Some(c - b'A' + 10),
        _ => None,
    }
}

fn b64val(c: u8) -> Option<u32> {
    match c {
        b'A'..=b'Z' => Some((c - b'A') as u32),
        b'a'..=b'z' => Some((c - b'a') as u32 + 26),
        b'0'..=b'9' => Some((c - b'0') as u32 + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

fn b64decode(d: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut acc: u32 = 0;
    let mut n = 0;
    for &c in d {
        if c == b'=' {
            break;
        }
        if let Some(v) = b64val(c) {
            acc = (acc << 6) | v;
            n += 1;
            if n == 4 {
                out.push((acc >> 16) as u8);
                out.push((acc >> 8) as u8);
                out.push(acc as u8);
                acc = 0;
                n = 0;
            }
        }
    }
    if n == 2 {
        out.push((acc >> 4) as u8);
    } else if n == 3 {
        out.push((acc >> 10) as u8);
        out.push((acc >> 2) as u8);
    }
    out
}

fn qpdecode(d: &[u8], nl: &[u8]) -> Vec<u8> {
    // join lines first
    let mut joined = Vec::new();
    let mut pos = 0;
    loop {
        let (line_end, next, has_nl) = match find(d, nl, pos) {
            Some(i) => (i, i + nl.len(), true),
            None => (d.len(), d.len(), false),
        };
        let mut e = line_end;
        while e > pos && is_ws(d[e - 1]) {
            e -= 1;
        }
        let line = &d[pos..e];
        if !line.is_empty() && line[line.len() - 1] == b'=' {
            joined.extend_from_slice(&line[..line.len() - 1]);
        } else {
            joined.extend_from_slice(line);
            if has_nl {
                joined.extend_from_slice(nl);
            }
        }
        if !has_nl {
            break;
        }
        pos = next;
    }
    let mut out = Vec::new();
    let mut i = 0;
    while i < joined.len() {
        let c = joined[i];
        if c == b'=' && i + 2 < joined.len() {
            if let (Some(a), Some(b)) = (hexv(joined[i + 1]), hexv(joined[i + 2])) {
                out.push(a * 16 + b);
                i += 3;
                continue;
            }
        }
        out.push(c);
        i += 1;
    }
    out
}

const CP1252: [u32; 32] = [
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039,
    0x0152, 0x008D, 0x017D, 0x008F, 0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
    0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178,
];

fn decode_charset(b: &[u8], cs: &str) -> String {
    let cs = cs.to_ascii_lowercase();
    match cs.as_str() {
        "iso-8859-1" | "latin1" | "iso8859-1" => b.iter().map(|&x| x as char).collect(),
        "windows-1252" | "cp1252" => b
            .iter()
            .map(|&x| {
                if (0x80..0xa0).contains(&x) {
                    char::from_u32(CP1252[(x - 0x80) as usize]).unwrap()
                } else {
                    x as char
                }
            })
            .collect(),
        _ => String::from_utf8_lossy(b).into_owned(),
    }
}

fn strip_lang(cs: &str) -> String {
    match cs.find('*') {
        Some(i) => cs[..i].to_string(),
        None => cs.to_string(),
    }
}

enum Seg {
    Text(String),
    Word(String, Vec<u8>),
}

fn decode_2047(s: &str) -> String {
    let b = s.as_bytes();
    let mut segs: Vec<Seg> = Vec::new();
    let mut text_start = 0;
    let mut i = 0;
    while i + 1 < b.len() {
        if b[i] == b'=' && b[i + 1] == b'?' {
            // try match
            let mut j = i + 2;
            while j < b.len() && b[j] != b'?' && !is_ws(b[j]) {
                j += 1;
            }
            if j > i + 2 && j < b.len() && b[j] == b'?' {
                let cs = &s[i + 2..j];
                if j + 2 < b.len() && matches!(b[j + 1], b'B' | b'b' | b'Q' | b'q') && b[j + 2] == b'?' {
                    let enc = b[j + 1];
                    let ts = j + 3;
                    let mut k = ts;
                    while k < b.len() && b[k] != b'?' && !is_ws(b[k]) {
                        k += 1;
                    }
                    if k + 1 < b.len() && b[k] == b'?' && b[k + 1] == b'=' {
                        let et = &b[ts..k];
                        let bytes = if enc == b'B' || enc == b'b' {
                            b64decode(et)
                        } else {
                            let mut o = Vec::new();
                            let mut p = 0;
                            while p < et.len() {
                                let c = et[p];
                                if c == b'_' {
                                    o.push(0x20);
                                } else if c == b'=' && p + 2 < et.len() && hexv(et[p + 1]).is_some() && hexv(et[p + 2]).is_some() {
                                    o.push(hexv(et[p + 1]).unwrap() * 16 + hexv(et[p + 2]).unwrap());
                                    p += 3;
                                    continue;
                                } else {
                                    o.push(c);
                                }
                                p += 1;
                            }
                            o
                        };
                        if text_start < i {
                            segs.push(Seg::Text(s[text_start..i].to_string()));
                        }
                        segs.push(Seg::Word(strip_lang(cs).to_ascii_lowercase(), bytes));
                        i = k + 2;
                        text_start = i;
                        continue;
                    }
                }
            }
        }
        i += 1;
    }
    if text_start < b.len() {
        segs.push(Seg::Text(s[text_start..].to_string()));
    }
    // remove whitespace-only text between words
    let mut cleaned: Vec<Seg> = Vec::new();
    let mut idx = 0;
    let mut prev_word = false;
    let mut buf: Vec<Seg> = segs;
    while idx < buf.len() {
        let drop = match &buf[idx] {
            Seg::Text(t) => {
                prev_word
                    && idx + 1 < buf.len()
                    && matches!(buf[idx + 1], Seg::Word(..))
                    && t.bytes().all(is_ws)
            }
            _ => false,
        };
        if !drop {
            prev_word = matches!(buf[idx], Seg::Word(..));
            let s = std::mem::replace(&mut buf[idx], Seg::Text(String::new()));
            cleaned.push(s);
        }
        idx += 1;
    }
    let mut out = String::new();
    let mut k = 0;
    while k < cleaned.len() {
        match &cleaned[k] {
            Seg::Text(t) => {
                out.push_str(t);
                k += 1;
            }
            Seg::Word(cs, _) => {
                let cs = cs.clone();
                let mut bytes = Vec::new();
                while k < cleaned.len() {
                    if let Seg::Word(c2, b2) = &cleaned[k] {
                        if *c2 == cs {
                            bytes.extend_from_slice(b2);
                            k += 1;
                            continue;
                        }
                    }
                    break;
                }
                out.push_str(&decode_charset(&bytes, &cs));
            }
        }
    }
    out
}

struct Ctype {
    main: String,
    params: Vec<(String, String)>,
}

fn parse_ctype(v: &str, has_sub: bool) -> Ctype {
    let b = v.as_bytes();
    let mut i = 0;
    let skip = |i: &mut usize| {
        while *i < b.len() && is_ws(b[*i]) {
            *i += 1;
        }
    };
    skip(&mut i);
    let st = i;
    while i < b.len() && b[i] != b'/' && b[i] != b';' && !is_ws(b[i]) {
        i += 1;
    }
    let mut main = v[st..i].to_ascii_lowercase();
    if has_sub {
        skip(&mut i);
        if i < b.len() && b[i] == b'/' {
            i += 1;
            skip(&mut i);
            let s2 = i;
            while i < b.len() && b[i] != b';' && !is_ws(b[i]) {
                i += 1;
            }
            main = format!("{}/{}", main, v[s2..i].to_ascii_lowercase());
        }
    }
    let mut params = Vec::new();
    loop {
        skip(&mut i);
        if i >= b.len() {
            break;
        }
        if b[i] != b';' {
            i += 1;
            continue;
        }
        i += 1;
        skip(&mut i);
        let a = i;
        while i < b.len() && b[i] != b'=' && b[i] != b';' && !is_ws(b[i]) {
            i += 1;
        }
        let attr = v[a..i].to_ascii_lowercase();
        skip(&mut i);
        if i < b.len() && b[i] == b'=' {
            i += 1;
            skip(&mut i);
            let mut val = Vec::new();
            if i < b.len() && b[i] == b'"' {
                i += 1;
                while i < b.len() && b[i] != b'"' {
                    if b[i] == b'\\' && i + 1 < b.len() {
                        i += 1;
                    }
                    val.push(b[i]);
                    i += 1;
                }
                if i < b.len() {
                    i += 1;
                }
            } else {
                while i < b.len() && b[i] != b';' && !is_ws(b[i]) {
                    val.push(b[i]);
                    i += 1;
                }
            }
            if !attr.is_empty() {
                params.push((attr, String::from_utf8_lossy(&val).into_owned()));
            }
        }
    }
    Ctype { main, params }
}

fn get_param<'a>(c: &'a Ctype, n: &str) -> Option<&'a str> {
    c.params.iter().find(|(a, _)| a == n).map(|(_, v)| v.as_str())
}

fn pct_decode(s: &str) -> Vec<u8> {
    let b = s.as_bytes();
    let mut o = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() && hexv(b[i + 1]).is_some() && hexv(b[i + 2]).is_some() {
            o.push(hexv(b[i + 1]).unwrap() * 16 + hexv(b[i + 2]).unwrap());
            i += 3;
        } else {
            o.push(b[i]);
            i += 1;
        }
    }
    o
}

fn split_cs(v: &str) -> (String, &str) {
    // charset'language'value
    if let Some(p1) = v.find('\'') {
        if let Some(p2) = v[p1 + 1..].find('\'') {
            return (v[..p1].to_string(), &v[p1 + 1 + p2 + 1..]);
        }
    }
    (String::new(), v)
}

fn rfc2231(c: &Ctype, a: &str) -> Option<String> {
    let single = format!("{}*", a);
    if let Some(v) = get_param(c, &single) {
        let (cs, rest) = split_cs(v);
        let bytes = pct_decode(rest);
        let cs = if cs.is_empty() { "us-ascii".to_string() } else { strip_lang(&cs) };
        return Some(decode_charset(&bytes, &cs));
    }
    let mut segs: Vec<(u64, bool, &str)> = Vec::new();
    let pre = format!("{}*", a);
    for (n, v) in &c.params {
        if let Some(rest) = n.strip_prefix(&pre) {
            let (num, enc) = match rest.strip_suffix('*') {
                Some(r) => (r, true),
                None => (rest, false),
            };
            if !num.is_empty() && num.bytes().all(|x| x.is_ascii_digit()) {
                if let Ok(k) = num.parse::<u64>() {
                    segs.push((k, enc, v.as_str()));
                }
            }
        }
    }
    if segs.is_empty() {
        return None;
    }
    segs.sort_by_key(|s| s.0);
    let mut cs = String::new();
    let mut bytes = Vec::new();
    let mut first_enc = true;
    for (_, enc, v) in segs {
        if enc {
            if first_enc {
                first_enc = false;
                let (c2, rest) = split_cs(v);
                cs = c2;
                bytes.extend(pct_decode(rest));
            } else {
                bytes.extend(pct_decode(v));
            }
        } else {
            bytes.extend_from_slice(v.as_bytes());
        }
    }
    let cs = if cs.is_empty() { "us-ascii".to_string() } else { strip_lang(&cs) };
    Some(decode_charset(&bytes, &cs))
}

fn crc32(d: &[u8]) -> u32 {
    let mut table = [0u32; 256];
    for i in 0..256u32 {
        let mut c = i;
        for _ in 0..8 {
            c = if c & 1 != 0 { 0xEDB88320 ^ (c >> 1) } else { c >> 1 };
        }
        table[i as usize] = c;
    }
    let mut c = 0xFFFFFFFFu32;
    for &b in d {
        c = table[((c ^ b as u32) & 0xff) as usize] ^ (c >> 8);
    }
    c ^ 0xFFFFFFFF
}

fn quote(s: &str) -> String {
    let mut o = String::from("\"");
    for ch in s.chars() {
        match ch {
            '\\' => o.push_str("\\\\"),
            '"' => o.push_str("\\\""),
            '\n' => o.push_str("\\n"),
            '\t' => o.push_str("\\t"),
            c if (c as u32) < 0x20 || ((c as u32) >= 0x7f && (c as u32) <= 0x9f) => {
                o.push_str(&format!("\\u{:04x}", c as u32))
            }
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

struct Entity<'a> {
    headers: HashMap<String, String>,
    body: &'a [u8],
}

fn parse_entity<'a>(d: &'a [u8], nl: &[u8]) -> Entity<'a> {
    let mut lines: Vec<String> = Vec::new();
    let mut pos = 0;
    let mut body: &[u8] = &d[d.len()..];
    loop {
        if pos >= d.len() {
            break;
        }
        let (le, next) = match find(d, nl, pos) {
            Some(i) => (i, i + nl.len()),
            None => (d.len(), d.len()),
        };
        if le == pos {
            body = &d[next.min(d.len())..];
            break;
        }
        let line = String::from_utf8_lossy(&d[pos..le]).into_owned();
        if (line.starts_with(' ') || line.starts_with('\t')) && !lines.is_empty() {
            let l = lines.last_mut().unwrap();
            l.push_str(&line);
        } else {
            lines.push(line);
        }
        pos = next;
    }
    let mut headers = HashMap::new();
    for l in lines {
        if let Some(c) = l.find(':') {
            let name = trim_ws(&l[..c]).to_ascii_lowercase();
            let val = trim_ws(&l[c + 1..]).to_string();
            headers.entry(name).or_insert(val);
        }
    }
    Entity { headers, body }
}

fn split_multipart<'a>(body: &'a [u8], bnd: &str, nl: &[u8]) -> Vec<&'a [u8]> {
    let delim = format!("--{}", bnd);
    let db = delim.as_bytes();
    let mut parts = Vec::new();
    let mut pos = 0;
    let mut cur_start: Option<usize> = None;
    let mut closed = false;
    while pos <= body.len() {
        let (le, next, has) = match find(body, nl, pos) {
            Some(i) => (i, i + nl.len(), true),
            None => (body.len(), body.len(), false),
        };
        let line = &body[pos..le];
        let mut kind = 0; // 0 none, 1 delim, 2 close
        if line.starts_with(db) {
            let rest = &line[db.len()..];
            if rest.iter().all(|&c| is_ws(c)) {
                kind = 1;
            } else if rest.starts_with(b"--") && rest[2..].iter().all(|&c| is_ws(c)) {
                kind = 2;
            }
        }
        if kind != 0 {
            if let Some(s) = cur_start {
                let end = if pos >= nl.len() + s { pos - nl.len() } else { s };
                let end = end.max(s);
                parts.push(&body[s..end]);
            }
            if kind == 2 {
                closed = true;
                break;
            }
            cur_start = Some(next);
        }
        if !has {
            break;
        }
        pos = next;
    }
    if !closed {
        if let Some(s) = cur_start {
            if s <= body.len() {
                parts.push(&body[s..]);
            }
        }
    }
    parts
}

fn render(d: &[u8], nl: &[u8], depth: usize, out: &mut String) {
    let e = parse_entity(d, nl);
    let ind = "  ".repeat(depth);
    let ct = match e.headers.get("content-type") {
        Some(v) => parse_ctype(v, true),
        None => Ctype { main: "text/plain".into(), params: vec![] },
    };
    let cte = e
        .headers
        .get("content-transfer-encoding")
        .map(|s| s.to_ascii_lowercase())
        .unwrap_or_else(|| "7bit".into());
    let cd = e.headers.get("content-disposition").map(|v| parse_ctype(v, false));
    let ty = ct.main.split('/').next().unwrap_or("").to_string();
    if ty == "multipart" {
        let bnd = get_param(&ct, "boundary").unwrap_or("").to_string();
        let parts = split_multipart(e.body, &bnd, nl);
        out.push_str(&format!("{}{} parts={}\n", ind, ct.main, parts.len()));
        for p in parts {
            render(p, nl, depth + 1, out);
        }
        return;
    }
    let raw = match cte.as_str() {
        "base64" => b64decode(e.body),
        "quoted-printable" => qpdecode(e.body, nl),
        _ => e.body.to_vec(),
    };
    let disp = cd.as_ref().map(|c| c.main.clone());
    if ty == "text" && disp.as_deref() != Some("attachment") {
        let cs = get_param(&ct, "charset").unwrap_or("us-ascii").to_ascii_lowercase();
        let txt = decode_charset(&raw, &cs);
        let txt = txt.replace("\r\n", "\n").replace('\r', "\n");
        out.push_str(&format!(
            "{}{} charset={} cte={} chars={}\n",
            ind,
            ct.main,
            cs,
            cte,
            txt.chars().count()
        ));
        out.push_str(&format!("{}  {}\n", ind, quote(&txt)));
    } else {
        let mut name: Option<String> = None;
        if let Some(c) = &cd {
            name = rfc2231(c, "filename");
            if name.is_none() {
                name = get_param(c, "filename").map(decode_2047);
            }
        }
        if name.is_none() {
            name = rfc2231(&ct, "name");
        }
        if name.is_none() {
            name = get_param(&ct, "name").map(decode_2047);
        }
        out.push_str(&format!(
            "{}{} cte={} disposition={} name={} size={} crc32={:08x}\n",
            ind,
            ct.main,
            cte,
            disp.unwrap_or_else(|| "-".into()),
            name.map(|n| quote(&n)).unwrap_or_else(|| "-".into()),
            raw.len(),
            crc32(&raw)
        ));
    }
}

fn main() {
    let dir = std::env::args().nth(1).unwrap();
    let mut names: Vec<String> = fs::read_dir(&dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".eml"))
        .collect();
    names.sort_by(|a, b| a.as_bytes().cmp(b.as_bytes()));
    let mut out = String::new();
    for (i, n) in names.iter().enumerate() {
        if i > 0 {
            out.push('\n');
        }
        let d = fs::read(format!("{}/{}", dir, n)).unwrap();
        let nl: &[u8] = match d.iter().position(|&c| c == b'\n') {
            Some(p) if p > 0 && d[p - 1] == b'\r' => b"\r\n",
            _ => b"\n",
        };
        let e = parse_entity(&d, nl);
        out.push_str(&format!("== {}\n", n));
        for h in ["from", "subject"] {
            let label = if h == "from" { "From" } else { "Subject" };
            match e.headers.get(h) {
                Some(v) => out.push_str(&format!("{}: {}\n", label, quote(&decode_2047(v)))),
                None => out.push_str(&format!("{}: -\n", label)),
            }
        }
        render(&d, nl, 0, &mut out);
    }
    std::io::stdout().write_all(out.as_bytes()).unwrap();
}
