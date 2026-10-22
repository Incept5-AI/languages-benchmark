use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpStream;

#[derive(Clone, Debug)]
enum J {
    Null,
    Bool(bool),
    Num(String),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(String, J)>),
}

impl J {
    fn get(&self, k: &str) -> Option<&J> {
        if let J::Obj(v) = self {
            for (kk, vv) in v.iter() {
                if kk == k {
                    return Some(vv);
                }
            }
        }
        None
    }
    fn as_str(&self) -> Option<&str> {
        if let J::Str(s) = self { Some(s) } else { None }
    }
}

struct P<'a> {
    s: &'a [u8],
    i: usize,
}

impl<'a> P<'a> {
    fn ws(&mut self) {
        while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }
    fn val(&mut self) -> J {
        self.ws();
        let c = self.s[self.i];
        match c {
            b'{' => {
                self.i += 1;
                let mut v = Vec::new();
                self.ws();
                if self.s[self.i] == b'}' {
                    self.i += 1;
                    return J::Obj(v);
                }
                loop {
                    self.ws();
                    let k = self.string();
                    self.ws();
                    self.i += 1;
                    let x = self.val();
                    v.push((k, x));
                    self.ws();
                    let d = self.s[self.i];
                    self.i += 1;
                    if d == b'}' {
                        return J::Obj(v);
                    }
                }
            }
            b'[' => {
                self.i += 1;
                let mut v = Vec::new();
                self.ws();
                if self.s[self.i] == b']' {
                    self.i += 1;
                    return J::Arr(v);
                }
                loop {
                    v.push(self.val());
                    self.ws();
                    let d = self.s[self.i];
                    self.i += 1;
                    if d == b']' {
                        return J::Arr(v);
                    }
                }
            }
            b'"' => J::Str(self.string()),
            b't' => {
                self.i += 4;
                J::Bool(true)
            }
            b'f' => {
                self.i += 5;
                J::Bool(false)
            }
            b'n' => {
                self.i += 4;
                J::Null
            }
            _ => {
                let st = self.i;
                while self.i < self.s.len() && b"+-0123456789.eE".contains(&self.s[self.i]) {
                    self.i += 1;
                }
                J::Num(String::from_utf8_lossy(&self.s[st..self.i]).into_owned())
            }
        }
    }
    fn hex4(&mut self) -> u32 {
        let h = std::str::from_utf8(&self.s[self.i..self.i + 4]).unwrap();
        self.i += 4;
        u32::from_str_radix(h, 16).unwrap()
    }
    fn string(&mut self) -> String {
        self.i += 1;
        let mut b: Vec<u8> = Vec::new();
        loop {
            let c = self.s[self.i];
            self.i += 1;
            if c == b'"' {
                return String::from_utf8_lossy(&b).into_owned();
            }
            if c == b'\\' {
                let e = self.s[self.i];
                self.i += 1;
                let ch: Option<char> = match e {
                    b'b' => Some('\u{8}'),
                    b'f' => Some('\u{c}'),
                    b'n' => Some('\n'),
                    b'r' => Some('\r'),
                    b't' => Some('\t'),
                    b'u' => {
                        let mut cp = self.hex4();
                        if (0xD800..0xDC00).contains(&cp)
                            && self.i + 6 <= self.s.len()
                            && self.s[self.i] == b'\\'
                            && self.s[self.i + 1] == b'u'
                        {
                            let save = self.i;
                            self.i += 2;
                            let lo = self.hex4();
                            if (0xDC00..0xE000).contains(&lo) {
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            } else {
                                self.i = save;
                            }
                        }
                        Some(char::from_u32(cp).unwrap_or('\u{FFFD}'))
                    }
                    other => Some(other as char),
                };
                if let Some(ch) = ch {
                    let mut buf = [0u8; 4];
                    b.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                }
            } else {
                b.push(c);
            }
        }
    }
}

fn parse(s: &str) -> J {
    P { s: s.as_bytes(), i: 0 }.val()
}

fn esc(b: &mut String, s: &str) {
    b.push('"');
    for c in s.chars() {
        match c {
            '"' => b.push_str("\\\""),
            '\\' => b.push_str("\\\\"),
            '\u{8}' => b.push_str("\\b"),
            '\u{c}' => b.push_str("\\f"),
            '\n' => b.push_str("\\n"),
            '\r' => b.push_str("\\r"),
            '\t' => b.push_str("\\t"),
            c if (c as u32) < 0x20 => b.push_str(&format!("\\u{:04x}", c as u32)),
            c => b.push(c),
        }
    }
    b.push('"');
}

fn write(b: &mut String, v: &J) {
    match v {
        J::Null => b.push_str("null"),
        J::Bool(x) => b.push_str(if *x { "true" } else { "false" }),
        J::Num(t) => b.push_str(t),
        J::Str(s) => esc(b, s),
        J::Arr(a) => {
            b.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    b.push(',');
                }
                write(b, x);
            }
            b.push(']');
        }
        J::Obj(o) => {
            b.push('{');
            for (i, (k, x)) in o.iter().enumerate() {
                if i > 0 {
                    b.push(',');
                }
                esc(b, k);
                b.push(':');
                write(b, x);
            }
            b.push('}');
        }
    }
}

fn csv(s: &str) -> Vec<Vec<String>> {
    let s = s.strip_prefix('\u{FEFF}').unwrap_or(s);
    let c: Vec<char> = s.chars().collect();
    let n = c.len();
    let mut i = 0;
    let mut rows = Vec::new();
    while i < n {
        let mut row = Vec::new();
        loop {
            let mut f = String::new();
            if i < n && c[i] == '"' {
                i += 1;
                while i < n {
                    if c[i] == '"' {
                        if i + 1 < n && c[i + 1] == '"' {
                            f.push('"');
                            i += 2;
                        } else {
                            i += 1;
                            break;
                        }
                    } else {
                        f.push(c[i]);
                        i += 1;
                    }
                }
            }
            while i < n && c[i] != ',' && c[i] != '\r' && c[i] != '\n' {
                f.push(c[i]);
                i += 1;
            }
            row.push(f);
            if i < n && c[i] == ',' {
                i += 1;
                continue;
            }
            if i < n && c[i] == '\r' {
                i += 1;
            }
            if i < n && c[i] == '\n' {
                i += 1;
            }
            break;
        }
        rows.push(row);
    }
    rows
}

fn norm_int(s: &str) -> String {
    let t = s.trim().trim_start_matches('0');
    if t.is_empty() { "0".to_string() } else { t.to_string() }
}

fn to_int(v: Option<&J>) -> i128 {
    match v {
        Some(J::Num(t)) => {
            if let Ok(x) = t.parse::<i128>() {
                x
            } else {
                t.parse::<f64>().map(|f| f as i128).unwrap_or(0)
            }
        }
        _ => 0,
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dir = std::path::Path::new(&args[1]);
    let base = &args[2];
    let rest = &base[base.find("://").unwrap() + 3..];
    let (hp, prefix) = match rest.find('/') {
        Some(p) => (&rest[..p], &rest[p..]),
        None => (rest, ""),
    };
    let (host, port) = match hp.rfind(':') {
        Some(p) => (&hp[..p], hp[p + 1..].parse::<u16>().unwrap()),
        None => (hp, 80u16),
    };

    let task = parse(&std::fs::read_to_string(dir.join("task.json")).unwrap());
    let rates_doc = parse(&std::fs::read_to_string(dir.join("rates.json")).unwrap());
    let rates = rates_doc.get("rates").unwrap().clone();
    let inv_bytes = std::fs::read(dir.join("inventory.csv")).unwrap();
    let rows = csv(&String::from_utf8_lossy(&inv_bytes));
    let mut inv: HashMap<String, Vec<String>> = HashMap::new();
    for r in rows.iter().skip(1) {
        if r.len() >= 5 {
            inv.insert(r[0].clone(), r.clone());
        }
    }
    let api_key = task.get("api_key").and_then(|x| x.as_str()).unwrap_or("").to_string();
    let mut messages: Vec<J> = vec![
        J::Obj(vec![
            ("role".into(), J::Str("system".into())),
            ("content".into(), task.get("system").cloned().unwrap_or(J::Null)),
        ]),
        J::Obj(vec![
            ("role".into(), J::Str("user".into())),
            ("content".into(), task.get("user").cloned().unwrap_or(J::Null)),
        ]),
    ];
    let mut out = String::new();
    let (mut pt, mut ct, mut tt): (i128, i128, i128) = (0, 0, 0);
    let mut requests = 0;
    let stdout = std::io::stdout();
    loop {
        let req = J::Obj(vec![
            ("model".into(), task.get("model").cloned().unwrap_or(J::Null)),
            ("messages".into(), J::Arr(messages.clone())),
            ("tools".into(), task.get("tools").cloned().unwrap_or(J::Arr(vec![]))),
            ("tool_choice".into(), J::Str("auto".into())),
        ]);
        let mut body = String::new();
        write(&mut body, &req);
        let mut sock = TcpStream::connect((host, port)).unwrap();
        let hdr = format!(
            "POST {}/chat/completions HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            prefix, hp, api_key, body.len()
        );
        sock.write_all(hdr.as_bytes()).unwrap();
        sock.write_all(body.as_bytes()).unwrap();
        sock.flush().unwrap();
        let mut all = Vec::new();
        let _ = sock.read_to_end(&mut all);
        requests += 1;
        let sep = all.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
        let head = String::from_utf8_lossy(&all[..sep]).into_owned();
        let mut lines = head.split("\r\n");
        let status: u32 = lines.next().unwrap().split(' ').nth(1).unwrap().parse().unwrap();
        let mut cl: Option<usize> = None;
        for l in lines {
            if let Some(p) = l.find(':') {
                if l[..p].trim().eq_ignore_ascii_case("content-length") {
                    cl = l[p + 1..].trim().parse().ok();
                }
            }
        }
        let start = sep + 4;
        let end = match cl {
            Some(c) => (start + c).min(all.len()),
            None => all.len(),
        };
        let rbody = String::from_utf8_lossy(&all[start..end]).into_owned();
        if status != 200 {
            let e = parse(&rbody);
            let m = e
                .get("error")
                .and_then(|x| x.get("message"))
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            out.push_str(&format!("ERROR {} {}\n", status, m));
            let mut so = stdout.lock();
            so.write_all(out.as_bytes()).unwrap();
            so.flush().unwrap();
            std::process::exit(0);
        }
        let resp = parse(&rbody);
        if let Some(u) = resp.get("usage") {
            pt += to_int(u.get("prompt_tokens"));
            ct += to_int(u.get("completion_tokens"));
            tt += to_int(u.get("total_tokens"));
        }
        let choice = match resp.get("choices") {
            Some(J::Arr(a)) => a[0].clone(),
            _ => J::Null,
        };
        let msg = choice.get("message").cloned().unwrap_or(J::Null);
        let fr = choice.get("finish_reason").and_then(|x| x.as_str()).unwrap_or("");
        if fr == "tool_calls" {
            messages.push(msg.clone());
            if let Some(J::Str(c)) = msg.get("content") {
                out.push_str(&format!("SAY {}\n", c));
            }
            let calls = match msg.get("tool_calls") {
                Some(J::Arr(a)) => a.clone(),
                _ => vec![],
            };
            let mut results = Vec::new();
            for call in calls.iter() {
                let id = call.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let f = call.get("function").cloned().unwrap_or(J::Null);
                let name = f.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let argstr = f.get("arguments").and_then(|x| x.as_str()).unwrap_or("{}").to_string();
                out.push_str(&format!("CALL {} {}\n", id, name));
                let mut b = String::new();
                if name == "inventory_lookup" {
                    let a = parse(&argstr);
                    let sku = a.get("sku").and_then(|x| x.as_str()).unwrap_or("").to_string();
                    match inv.get(&sku) {
                        None => {
                            b.push_str("{\"error\":");
                            esc(&mut b, &format!("unknown sku: {}", sku));
                            b.push('}');
                        }
                        Some(r) => {
                            b.push_str("{\"sku\":");
                            esc(&mut b, &r[0]);
                            b.push_str(",\"name\":");
                            esc(&mut b, &r[1]);
                            b.push_str(",\"price_cents\":");
                            b.push_str(&norm_int(&r[2]));
                            b.push_str(",\"currency\":");
                            esc(&mut b, &r[3]);
                            b.push_str(",\"stock\":");
                            b.push_str(&norm_int(&r[4]));
                            b.push('}');
                        }
                    }
                } else if name == "convert_currency" {
                    let a = parse(&argstr);
                    let amt = to_int(a.get("amount_cents"));
                    let from = a.get("from").and_then(|x| x.as_str()).unwrap_or("").to_string();
                    let to = a.get("to").and_then(|x| x.as_str()).unwrap_or("").to_string();
                    let rf = to_int(rates.get(&from));
                    let rt = to_int(rates.get(&to));
                    let conv = (2 * amt * rt + rf) / (2 * rf);
                    b.push_str(&format!("{{\"amount_cents\":{},\"currency\":", conv));
                    esc(&mut b, &to);
                    b.push('}');
                } else {
                    b.push_str("{\"error\":");
                    esc(&mut b, &format!("unknown tool: {}", name));
                    b.push('}');
                }
                messages.push(J::Obj(vec![
                    ("role".into(), J::Str("tool".into())),
                    ("tool_call_id".into(), J::Str(id.clone())),
                    ("content".into(), J::Str(b.clone())),
                ]));
                results.push((id, b));
            }
            for (id, r) in results {
                out.push_str(&format!("RESULT {} {}\n", id, r));
            }
        } else {
            let c = msg.get("content").and_then(|x| x.as_str()).unwrap_or("").to_string();
            out.push_str(&format!("FINAL {}\n", c));
            break;
        }
    }
    out.push_str(&format!(
        "USAGE prompt={} completion={} total={} requests={}\n",
        pt, ct, tt, requests
    ));
    let mut so = stdout.lock();
    so.write_all(out.as_bytes()).unwrap();
    so.flush().unwrap();
}
