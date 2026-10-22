use std::collections::{BTreeMap, HashMap};
use std::io::{Read, Write};
use std::net::TcpStream;

#[derive(Debug, Clone)]
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
            for (kk, vv) in v {
                if kk == k {
                    return Some(vv);
                }
            }
        }
        None
    }
    fn s(&self) -> String {
        match self {
            J::Str(s) => s.clone(),
            J::Num(s) => s.clone(),
            _ => String::new(),
        }
    }
}

struct P<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> P<'a> {
    fn ws(&mut self) {
        while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\r' | b'\n') {
            self.i += 1;
        }
    }
    fn val(&mut self) -> J {
        self.ws();
        match self.b[self.i] {
            b'{' => {
                self.i += 1;
                let mut v = Vec::new();
                self.ws();
                if self.b[self.i] == b'}' {
                    self.i += 1;
                    return J::Obj(v);
                }
                loop {
                    self.ws();
                    let k = self.str();
                    self.ws();
                    self.i += 1;
                    let x = self.val();
                    v.push((k, x));
                    self.ws();
                    let c = self.b[self.i];
                    self.i += 1;
                    if c == b'}' {
                        return J::Obj(v);
                    }
                }
            }
            b'[' => {
                self.i += 1;
                let mut v = Vec::new();
                self.ws();
                if self.b[self.i] == b']' {
                    self.i += 1;
                    return J::Arr(v);
                }
                loop {
                    v.push(self.val());
                    self.ws();
                    let c = self.b[self.i];
                    self.i += 1;
                    if c == b']' {
                        return J::Arr(v);
                    }
                }
            }
            b'"' => J::Str(self.str()),
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
                while self.i < self.b.len() && b"+-0123456789.eE".contains(&self.b[self.i]) {
                    self.i += 1;
                }
                J::Num(String::from_utf8_lossy(&self.b[st..self.i]).into_owned())
            }
        }
    }
    fn hex4(&mut self) -> u32 {
        let s = std::str::from_utf8(&self.b[self.i..self.i + 4]).unwrap();
        self.i += 4;
        u32::from_str_radix(s, 16).unwrap()
    }
    fn str(&mut self) -> String {
        self.i += 1;
        let mut out: Vec<u8> = Vec::new();
        loop {
            let c = self.b[self.i];
            self.i += 1;
            if c == b'"' {
                break;
            }
            if c == b'\\' {
                let e = self.b[self.i];
                self.i += 1;
                match e {
                    b'n' => out.push(b'\n'),
                    b't' => out.push(b'\t'),
                    b'r' => out.push(b'\r'),
                    b'b' => out.push(8),
                    b'f' => out.push(12),
                    b'u' => {
                        let mut cp = self.hex4();
                        if (0xD800..0xDC00).contains(&cp)
                            && self.i + 1 < self.b.len()
                            && self.b[self.i] == b'\\'
                            && self.b[self.i + 1] == b'u'
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
                        let ch = char::from_u32(cp).unwrap_or('\u{FFFD}');
                        let mut buf = [0u8; 4];
                        out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                    }
                    other => out.push(other),
                }
            } else {
                out.push(c);
            }
        }
        String::from_utf8_lossy(&out).into_owned()
    }
}

fn parse(b: &[u8]) -> J {
    P { b, i: 0 }.val()
}

struct Resp {
    status: u32,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

fn find(a: &[u8], pat: &[u8], from: usize) -> Option<usize> {
    if a.len() < pat.len() {
        return None;
    }
    (from..=a.len() - pat.len()).find(|&i| &a[i..i + pat.len()] == pat)
}

fn try_parse(a: &[u8], eof: bool) -> Option<Resp> {
    let he = find(a, b"\r\n\r\n", 0)?;
    let head = String::from_utf8_lossy(&a[..he]).into_owned();
    let mut lines = head.split("\r\n");
    let sl = lines.next().unwrap();
    let status: u32 = sl.split(' ').nth(1).unwrap_or("0").trim().parse().unwrap_or(0);
    let mut headers = HashMap::new();
    for l in lines {
        if let Some(c) = l.find(':') {
            headers.insert(l[..c].trim().to_ascii_lowercase(), l[c + 1..].trim().to_string());
        }
    }
    let mut p = he + 4;
    let chunked = headers
        .get("transfer-encoding")
        .map(|v| v.to_ascii_lowercase().contains("chunked"))
        .unwrap_or(false);
    if chunked {
        let mut body = Vec::new();
        loop {
            let le = match find(a, b"\r\n", p) {
                Some(x) => x,
                None => {
                    return if eof { Some(Resp { status, headers, body }) } else { None };
                }
            };
            let mut szs = String::from_utf8_lossy(&a[p..le]).into_owned();
            if let Some(s) = szs.find(';') {
                szs.truncate(s);
            }
            let sz = usize::from_str_radix(szs.trim(), 16).unwrap_or(0);
            p = le + 2;
            if sz == 0 {
                loop {
                    match find(a, b"\r\n", p) {
                        None => {
                            return if eof { Some(Resp { status, headers, body }) } else { None };
                        }
                        Some(t) if t == p => return Some(Resp { status, headers, body }),
                        Some(t) => p = t + 2,
                    }
                }
            }
            if p + sz > a.len() {
                if !eof {
                    return None;
                }
                body.extend_from_slice(&a[p..]);
                return Some(Resp { status, headers, body });
            }
            body.extend_from_slice(&a[p..p + sz]);
            p += sz;
            if p + 2 > a.len() {
                return if eof { Some(Resp { status, headers, body }) } else { None };
            }
            p += 2;
        }
    }
    if let Some(cl) = headers.get("content-length") {
        let mut n: usize = cl.trim().parse().unwrap_or(0);
        if a.len() - p < n {
            if !eof {
                return None;
            }
            n = a.len() - p;
        }
        let body = a[p..p + n].to_vec();
        return Some(Resp { status, headers, body });
    }
    if !eof {
        return None;
    }
    Some(Resp { status, headers, body: a[p..].to_vec() })
}

struct Client {
    host: String,
    port: u16,
}

impl Client {
    fn request(&self, method: &str, path: &str, hdrs: &[(&str, String)], body: Option<&[u8]>) -> Resp {
        let mut s = TcpStream::connect((self.host.as_str(), self.port)).expect("connect");
        let mut req = format!("{} {} HTTP/1.1\r\nHost: {}:{}\r\n", method, path, self.host, self.port);
        for (k, v) in hdrs {
            req.push_str(&format!("{}: {}\r\n", k, v));
        }
        if let Some(b) = body {
            req.push_str(&format!("Content-Length: {}\r\n", b.len()));
        }
        req.push_str("Connection: close\r\n\r\n");
        let mut all = req.into_bytes();
        if let Some(b) = body {
            all.extend_from_slice(b);
        }
        s.write_all(&all).unwrap();
        s.flush().unwrap();
        let mut data = Vec::new();
        let mut buf = [0u8; 65536];
        loop {
            let n = s.read(&mut buf).unwrap_or(0);
            if n == 0 {
                break;
            }
            data.extend_from_slice(&buf[..n]);
            if let Some(r) = try_parse(&data, false) {
                return r;
            }
        }
        try_parse(&data, true).expect("bad response")
    }
}

fn form_enc(v: &str) -> String {
    let mut o = String::new();
    for &b in v.as_bytes() {
        if b.is_ascii_alphanumeric() || b == b'-' || b == b'.' || b == b'_' || b == b'*' {
            o.push(b as char);
        } else if b == b' ' {
            o.push('+');
        } else {
            o.push_str(&format!("%{:02X}", b));
        }
    }
    o
}

fn pct_enc(v: &str) -> String {
    let mut o = String::new();
    for &b in v.as_bytes() {
        if b.is_ascii_alphanumeric() || b == b'-' || b == b'.' || b == b'_' || b == b'~' {
            o.push(b as char);
        } else {
            o.push_str(&format!("%{:02X}", b));
        }
    }
    o
}

fn b64(d: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut o = String::new();
    for ch in d.chunks(3) {
        let b0 = ch[0] as u32;
        let b1 = if ch.len() > 1 { ch[1] as u32 } else { 0 };
        let b2 = if ch.len() > 2 { ch[2] as u32 } else { 0 };
        let n = (b0 << 16) | (b1 << 8) | b2;
        o.push(T[((n >> 18) & 63) as usize] as char);
        o.push(T[((n >> 12) & 63) as usize] as char);
        o.push(if ch.len() > 1 { T[((n >> 6) & 63) as usize] as char } else { '=' });
        o.push(if ch.len() > 2 { T[(n & 63) as usize] as char } else { '=' });
    }
    o
}

fn cents(a: &str) -> i64 {
    let (neg, a) = if let Some(r) = a.strip_prefix('-') { (true, r) } else { (false, a) };
    let (ip, fp) = match a.find('.') {
        Some(d) => (&a[..d], a[d + 1..].to_string()),
        None => (a, String::new()),
    };
    let mut fp = fp;
    while fp.len() < 2 {
        fp.push('0');
    }
    let v = ip.parse::<i64>().unwrap() * 100 + fp.parse::<i64>().unwrap();
    if neg {
        -v
    } else {
        v
    }
}

fn fmt(c: i64) -> String {
    let a = c.unsigned_abs();
    format!("{}{}.{:02}", if c < 0 { "-" } else { "" }, a / 100, a % 100)
}

struct Cfg {
    id: String,
    secret: String,
    scope: String,
}

fn get_token(cl: &Client, base: &str, cfg: &Cfg) -> Result<String, String> {
    let basic = b64(format!("{}:{}", form_enc(&cfg.id), form_enc(&cfg.secret)).as_bytes());
    let body = format!("grant_type=client_credentials&scope={}", form_enc(&cfg.scope));
    let r = cl.request(
        "POST",
        &format!("{}/oauth/token", base),
        &[
            ("Authorization", format!("Basic {}", basic)),
            ("Content-Type", "application/x-www-form-urlencoded".to_string()),
            ("Accept", "application/json".to_string()),
        ],
        Some(body.as_bytes()),
    );
    if r.status != 200 {
        return Err(format!("ERROR {} {}", r.status, String::from_utf8_lossy(&r.body)));
    }
    Ok(parse(&r.body).get("access_token").map(|j| j.s()).unwrap_or_default())
}

fn run(cl: &Client, base: &str, cfg: &Cfg, page_size: &str) -> Result<String, String> {
    let mut totals: HashMap<(String, String), i64> = HashMap::new();
    let mut statuses: BTreeMap<String, u64> = BTreeMap::new();
    let (mut n, mut pages, mut retries, mut reauth) = (0u64, 0u64, 0u64, 0u64);
    let mut token = get_token(cl, base, cfg)?;
    let mut fresh = true;
    let mut cursor: Option<String> = None;
    loop {
        let mut p = format!("{}/orders?limit={}", base, page_size);
        if let Some(c) = &cursor {
            p.push_str("&cursor=");
            p.push_str(&pct_enc(c));
        }
        let r = cl.request(
            "GET",
            &p,
            &[
                ("Authorization", format!("Bearer {}", token)),
                ("Accept", "application/json".to_string()),
            ],
            None,
        );
        let was_fresh = fresh;
        fresh = false;
        match r.status {
            200 => {
                pages += 1;
                let m = parse(&r.body);
                if let Some(J::Arr(items)) = m.get("data") {
                    for o in items {
                        n += 1;
                        let st = o.get("status").map(|j| j.s()).unwrap_or_default();
                        *statuses.entry(st.clone()).or_insert(0) += 1;
                        if st != "cancelled" {
                            let name = o.get("customer").and_then(|c| c.get("name")).map(|j| j.s()).unwrap_or_default();
                            let cur = o.get("currency").map(|j| j.s()).unwrap_or_default();
                            let amt = cents(&o.get("amount").map(|j| j.s()).unwrap_or_default());
                            *totals.entry((name, cur)).or_insert(0) += amt;
                        }
                    }
                }
                match m.get("next_cursor") {
                    Some(J::Str(s)) => cursor = Some(s.clone()),
                    _ => break,
                }
            }
            429 => {
                retries += 1;
                let secs: u64 = r.headers.get("retry-after").and_then(|v| v.trim().parse().ok()).unwrap_or(1);
                std::thread::sleep(std::time::Duration::from_millis(secs * 1000 + 50));
            }
            503 => {
                retries += 1;
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            401 if !was_fresh => {
                token = get_token(cl, base, cfg)?;
                reauth += 1;
                fresh = true;
            }
            _ => return Err(format!("ERROR {} {}", r.status, String::from_utf8_lossy(&r.body))),
        }
    }
    let mut keys: Vec<&(String, String)> = totals.keys().collect();
    keys.sort_by(|a, b| a.0.as_bytes().cmp(b.0.as_bytes()).then(a.1.as_bytes().cmp(b.1.as_bytes())));
    let mut out = String::new();
    for k in keys {
        out.push_str(&format!("total {} {} {}\n", k.1, fmt(totals[k]), k.0));
    }
    for (s, c) in &statuses {
        out.push_str(&format!("status {} {}\n", s, c));
    }
    out.push_str(&format!("orders {} pages {} retries {} reauth {}\n", n, pages, retries, reauth));
    Ok(out)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cfgb = std::fs::read(format!("{}/client.json", args[1])).unwrap();
    let cj = parse(&cfgb);
    let cfg = Cfg {
        id: cj.get("client_id").map(|j| j.s()).unwrap_or_default(),
        secret: cj.get("client_secret").map(|j| j.s()).unwrap_or_default(),
        scope: cj.get("scope").map(|j| j.s()).unwrap_or_default(),
    };
    let ps = cj.get("page_size").map(|j| j.s()).unwrap_or_default();
    let ps = ps.parse::<f64>().map(|f| format!("{}", f as i64)).unwrap_or(ps);
    let url = args[2].trim_end_matches('/');
    let rest = url.strip_prefix("http://").unwrap_or(url);
    let (hp, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, ""),
    };
    let (host, port) = match hp.rfind(':') {
        Some(i) => (hp[..i].to_string(), hp[i + 1..].parse().unwrap()),
        None => (hp.to_string(), 80u16),
    };
    let cl = Client { host, port };
    let out = match run(&cl, path, &cfg, &ps) {
        Ok(s) => s,
        Err(e) => format!("{}\n", e),
    };
    let so = std::io::stdout();
    let mut l = so.lock();
    l.write_all(out.as_bytes()).unwrap();
    l.flush().unwrap();
}
