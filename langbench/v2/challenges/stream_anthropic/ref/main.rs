use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Read, Write};
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
            for (kk, vv) in v {
                if kk == k {
                    return Some(vv);
                }
            }
        }
        None
    }
    fn s(&self, k: &str) -> String {
        match self.get(k) {
            Some(J::Str(s)) => s.clone(),
            _ => String::new(),
        }
    }
    fn n(&self, k: &str) -> i64 {
        match self.get(k) {
            Some(J::Num(s)) => s.parse::<i64>().unwrap_or_else(|_| s.parse::<f64>().unwrap_or(0.0) as i64),
            _ => 0,
        }
    }
}

struct P<'a> {
    b: &'a [u8],
    p: usize,
}

impl<'a> P<'a> {
    fn ws(&mut self) {
        while self.p < self.b.len() && matches!(self.b[self.p], b' ' | b'\t' | b'\r' | b'\n') {
            self.p += 1;
        }
    }
    fn peek(&self) -> Result<u8, String> {
        self.b.get(self.p).copied().ok_or("eof".to_string())
    }
    fn parse_all(&mut self) -> Result<J, String> {
        let v = self.val()?;
        self.ws();
        if self.p != self.b.len() {
            return Err("trailing".into());
        }
        Ok(v)
    }
    fn val(&mut self) -> Result<J, String> {
        self.ws();
        let c = self.peek()?;
        match c {
            b'{' => {
                self.p += 1;
                let mut m = Vec::new();
                self.ws();
                if self.peek()? == b'}' {
                    self.p += 1;
                    return Ok(J::Obj(m));
                }
                loop {
                    self.ws();
                    let k = self.string()?;
                    self.ws();
                    if self.peek()? != b':' {
                        return Err("colon".into());
                    }
                    self.p += 1;
                    let v = self.val()?;
                    m.push((k, v));
                    self.ws();
                    let d = self.peek()?;
                    self.p += 1;
                    if d == b'}' {
                        return Ok(J::Obj(m));
                    }
                    if d != b',' {
                        return Err("comma".into());
                    }
                }
            }
            b'[' => {
                self.p += 1;
                let mut a = Vec::new();
                self.ws();
                if self.peek()? == b']' {
                    self.p += 1;
                    return Ok(J::Arr(a));
                }
                loop {
                    a.push(self.val()?);
                    self.ws();
                    let d = self.peek()?;
                    self.p += 1;
                    if d == b']' {
                        return Ok(J::Arr(a));
                    }
                    if d != b',' {
                        return Err("comma".into());
                    }
                }
            }
            b'"' => Ok(J::Str(self.string()?)),
            _ => {
                let rest = &self.b[self.p..];
                if rest.starts_with(b"true") {
                    self.p += 4;
                    return Ok(J::Bool(true));
                }
                if rest.starts_with(b"false") {
                    self.p += 5;
                    return Ok(J::Bool(false));
                }
                if rest.starts_with(b"null") {
                    self.p += 4;
                    return Ok(J::Null);
                }
                let st = self.p;
                while self.p < self.b.len() && b"+-0123456789.eE".contains(&self.b[self.p]) {
                    self.p += 1;
                }
                if st == self.p {
                    return Err("bad".into());
                }
                Ok(J::Num(String::from_utf8_lossy(&self.b[st..self.p]).into_owned()))
            }
        }
    }
    fn hex4(&mut self) -> Result<u32, String> {
        if self.p + 4 > self.b.len() {
            return Err("eof".into());
        }
        let s = std::str::from_utf8(&self.b[self.p..self.p + 4]).map_err(|_| "hex")?;
        let v = u32::from_str_radix(s, 16).map_err(|_| "hex")?;
        self.p += 4;
        Ok(v)
    }
    fn string(&mut self) -> Result<String, String> {
        if self.peek()? != b'"' {
            return Err("str".into());
        }
        self.p += 1;
        let mut out: Vec<u8> = Vec::new();
        loop {
            let c = self.peek()?;
            self.p += 1;
            match c {
                b'"' => return Ok(String::from_utf8_lossy(&out).into_owned()),
                b'\\' => {
                    let e = self.peek()?;
                    self.p += 1;
                    match e {
                        b'n' => out.push(b'\n'),
                        b'r' => out.push(b'\r'),
                        b't' => out.push(b'\t'),
                        b'b' => out.push(8),
                        b'f' => out.push(12),
                        b'u' => {
                            let mut cp = self.hex4()?;
                            if (0xD800..0xDC00).contains(&cp)
                                && self.b.get(self.p) == Some(&b'\\')
                                && self.b.get(self.p + 1) == Some(&b'u')
                            {
                                let save = self.p;
                                self.p += 2;
                                let lo = self.hex4()?;
                                if (0xDC00..0xE000).contains(&lo) {
                                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                                } else {
                                    self.p = save;
                                }
                            }
                            let ch = char::from_u32(cp).unwrap_or('\u{FFFD}');
                            let mut buf = [0u8; 4];
                            out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                        }
                        other => out.push(other),
                    }
                }
                _ => out.push(c),
            }
        }
    }
}

fn parse(s: &str) -> Result<J, String> {
    P { b: s.as_bytes(), p: 0 }.parse_all()
}

fn ser_str(s: &str, o: &mut String) {
    o.push('"');
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            '\r' => o.push_str("\\r"),
            '\t' => o.push_str("\\t"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
}

fn ser(v: &J, o: &mut String) {
    match v {
        J::Null => o.push_str("null"),
        J::Bool(b) => o.push_str(if *b { "true" } else { "false" }),
        J::Num(n) => o.push_str(n),
        J::Str(s) => ser_str(s, o),
        J::Arr(a) => {
            o.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    o.push(',');
                }
                ser(x, o);
            }
            o.push(']');
        }
        J::Obj(m) => {
            o.push('{');
            for (i, (k, x)) in m.iter().enumerate() {
                if i > 0 {
                    o.push(',');
                }
                ser_str(k, o);
                o.push(':');
                ser(x, o);
            }
            o.push('}');
        }
    }
}

fn to_json(v: &J) -> String {
    let mut s = String::new();
    ser(v, &mut s);
    s
}

fn esc(s: &str) -> String {
    let mut o = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            '\r' => o.push_str("\\r"),
            '\t' => o.push_str("\\t"),
            c => o.push(c),
        }
    }
    o
}

fn obj(pairs: Vec<(&str, J)>) -> J {
    J::Obj(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

struct Ctx {
    host: String,
    port: u16,
    prefix: String,
    key: String,
}

fn read_line<R: BufRead>(r: &mut R) -> Option<String> {
    let mut v = Vec::new();
    let n = r.read_until(b'\n', &mut v).ok()?;
    if n == 0 {
        return None;
    }
    if v.last() == Some(&b'\n') {
        v.pop();
    }
    if v.last() == Some(&b'\r') {
        v.pop();
    }
    Some(String::from_utf8_lossy(&v).into_owned())
}

fn post(ctx: &Ctx, body: &[u8]) -> std::io::Result<(u32, Vec<u8>)> {
    let mut s = TcpStream::connect((ctx.host.as_str(), ctx.port))?;
    let head = format!(
        "POST {}/v1/messages HTTP/1.1\r\nHost: {}:{}\r\nContent-Length: {}\r\nx-api-key: {}\r\nanthropic-version: 2023-06-01\r\ncontent-type: application/json\r\n\r\n",
        ctx.prefix, ctx.host, ctx.port, body.len(), ctx.key
    );
    let mut req = head.into_bytes();
    req.extend_from_slice(body);
    s.write_all(&req)?;
    s.flush()?;
    let mut r = BufReader::new(s);
    let sl = read_line(&mut r).unwrap_or_default();
    let status: u32 = sl.split(' ').nth(1).and_then(|x| x.parse().ok()).unwrap_or(0);
    let mut cl: Option<usize> = None;
    let mut chunked = false;
    while let Some(h) = read_line(&mut r) {
        if h.is_empty() {
            break;
        }
        if let Some(i) = h.find(':') {
            let k = h[..i].trim().to_ascii_lowercase();
            let v = h[i + 1..].trim();
            if k == "content-length" {
                cl = v.parse().ok();
            }
            if k == "transfer-encoding" && v.to_ascii_lowercase().contains("chunked") {
                chunked = true;
            }
        }
    }
    let mut out = Vec::new();
    if chunked {
        loop {
            let line = match read_line(&mut r) {
                Some(l) => l,
                None => break,
            };
            let line = line.split(';').next().unwrap().trim().to_string();
            if line.is_empty() {
                continue;
            }
            let sz = match usize::from_str_radix(&line, 16) {
                Ok(x) => x,
                Err(_) => break,
            };
            if sz == 0 {
                break;
            }
            let mut buf = vec![0u8; sz];
            let mut off = 0;
            while off < sz {
                let n = r.read(&mut buf[off..])?;
                if n == 0 {
                    break;
                }
                off += n;
            }
            out.extend_from_slice(&buf[..off]);
            if off < sz {
                break;
            }
            read_line(&mut r);
        }
    } else if let Some(n) = cl {
        let mut buf = vec![0u8; n];
        let mut off = 0;
        while off < n {
            let k = r.read(&mut buf[off..])?;
            if k == 0 {
                break;
            }
            off += k;
        }
        out.extend_from_slice(&buf[..off]);
    } else {
        r.read_to_end(&mut out)?;
    }
    Ok((status, out))
}

#[derive(Default)]
struct Block {
    ty: String,
    text: String,
    sig: String,
    id: String,
    name: String,
    json: String,
}

#[derive(Default)]
struct Usage {
    i: i64,
    o: i64,
    c: i64,
}

fn dispatch(ev: &Option<String>, data: &[String], blocks: &mut BTreeMap<i64, Block>, stop: &mut String, u: &mut Usage) {
    if data.is_empty() {
        return;
    }
    let o = match parse(&data.join("\n")) {
        Ok(v) => v,
        Err(_) => return,
    };
    let t = match ev {
        Some(e) => e.clone(),
        None => o.s("type"),
    };
    match t.as_str() {
        "message_start" => {
            if let Some(us) = o.get("message").and_then(|m| m.get("usage")) {
                u.i += us.n("input_tokens");
                u.c += us.n("cache_read_input_tokens");
            }
        }
        "content_block_start" => {
            if let Some(cb) = o.get("content_block") {
                blocks.insert(
                    o.n("index"),
                    Block { ty: cb.s("type"), id: cb.s("id"), name: cb.s("name"), ..Default::default() },
                );
            }
        }
        "content_block_delta" => {
            if let (Some(b), Some(d)) = (blocks.get_mut(&o.n("index")), o.get("delta")) {
                match d.s("type").as_str() {
                    "thinking_delta" => b.text.push_str(&d.s("thinking")),
                    "signature_delta" => b.sig.push_str(&d.s("signature")),
                    "text_delta" => b.text.push_str(&d.s("text")),
                    "input_json_delta" => b.json.push_str(&d.s("partial_json")),
                    _ => {}
                }
            }
        }
        "message_delta" => {
            if let Some(J::Str(s)) = o.get("delta").and_then(|d| d.get("stop_reason")) {
                *stop = s.clone();
            }
            if let Some(us) = o.get("usage") {
                u.o += us.n("output_tokens");
            }
        }
        _ => {}
    }
}

fn parse_sse(body: &[u8], u: &mut Usage) -> (Vec<Block>, String) {
    let text = String::from_utf8_lossy(body).into_owned();
    let lines: Vec<&str> = text.split('\n').collect();
    let mut blocks = BTreeMap::new();
    let mut stop = String::new();
    let mut ev: Option<String> = None;
    let mut data: Vec<String> = Vec::new();
    for (i, l) in lines.iter().enumerate() {
        if i == lines.len() - 1 && l.is_empty() {
            break;
        }
        let line = l.strip_suffix('\r').unwrap_or(l);
        if line.is_empty() {
            dispatch(&ev, &data, &mut blocks, &mut stop, u);
            ev = None;
            data.clear();
            continue;
        }
        if line.starts_with(':') {
            continue;
        }
        let (f, v) = match line.find(':') {
            Some(c) => {
                let v = &line[c + 1..];
                (&line[..c], v.strip_prefix(' ').unwrap_or(v))
            }
            None => (line, ""),
        };
        if f == "event" {
            ev = Some(v.to_string());
        } else if f == "data" {
            data.push(v.to_string());
        }
    }
    dispatch(&ev, &data, &mut blocks, &mut stop, u);
    (blocks.into_values().collect(), stop)
}

fn print_reply(out: &mut String, bl: &[Block], stop: &str) {
    for b in bl {
        match b.ty.as_str() {
            "thinking" => {
                out.push_str(&format!("thinking {}\nsignature {}\n", esc(&b.text), esc(&b.sig)));
            }
            "text" => out.push_str(&format!("text {}\n", esc(&b.text))),
            "tool_use" => out.push_str(&format!("tool_use {} {} {}\n", esc(&b.id), esc(&b.name), esc(&b.json))),
            _ => {}
        }
    }
    out.push_str(&format!("stop_reason {}\n", esc(stop)));
}

fn run_tool(name: &str, input: &J) -> (bool, String) {
    match name {
        "measure" => {
            let text = input.s("text");
            let unit = match input.get("unit") {
                Some(J::Str(s)) => s.clone(),
                Some(v) => to_json(v),
                None => String::new(),
            };
            match unit.as_str() {
                "code_points" => (true, text.chars().count().to_string()),
                "utf8_bytes" => (true, text.len().to_string()),
                "utf16_units" => (true, text.encode_utf16().count().to_string()),
                _ => (false, format!("unsupported unit: {}", unit)),
            }
        }
        "reverse" => (true, input.s("text").chars().rev().collect()),
        _ => (false, format!("unknown tool: {}", name)),
    }
}

fn handle_error(out: &mut String, status: u32, body: &[u8]) -> bool {
    if status == 200 {
        return false;
    }
    let (mut et, mut em) = (String::new(), String::new());
    if let Ok(o) = parse(&String::from_utf8_lossy(body)) {
        if let Some(e) = o.get("error") {
            et = e.s("type");
            em = e.s("message");
        }
    }
    out.push_str(&format!("ERROR {} {}: {}\n", status, esc(&et), esc(&em)));
    true
}

fn make_body(req: &J, msgs: &[J]) -> Vec<u8> {
    let g = |k: &str| req.get(k).cloned().unwrap_or(J::Null);
    let b = obj(vec![
        ("model", g("model")),
        ("max_tokens", g("max_tokens")),
        ("stream", J::Bool(true)),
        ("thinking", obj(vec![("type", J::Str("enabled".into())), ("budget_tokens", g("thinking_budget"))])),
        ("system", g("system")),
        ("tools", g("tools")),
        ("messages", J::Arr(msgs.to_vec())),
    ]);
    to_json(&b).into_bytes()
}

fn run(out: &mut String) -> Result<(), String> {
    let args: Vec<String> = std::env::args().collect();
    let data_dir = &args[1];
    let base = &args[2];
    let rest = base.strip_prefix("http://").ok_or("url")?;
    let (hp, prefix) = match rest.find('/') {
        Some(i) => (&rest[..i], rest[i..].trim_end_matches('/').to_string()),
        None => (rest, String::new()),
    };
    let (host, port) = match hp.rfind(':') {
        Some(i) => (hp[..i].to_string(), hp[i + 1..].parse::<u16>().map_err(|_| "port")?),
        None => (hp.to_string(), 80),
    };
    let rtext = std::fs::read_to_string(format!("{}/request.json", data_dir)).map_err(|e| e.to_string())?;
    let req = parse(&rtext)?;
    let ctx = Ctx { host, port, prefix, key: req.s("api_key") };
    let mut u = Usage::default();
    let user = obj(vec![("role", J::Str("user".into())), ("content", req.get("prompt").cloned().unwrap_or(J::Null))]);

    out.push_str("request 1\n");
    let mut msgs = vec![user];
    let (st, body) = post(&ctx, &make_body(&req, &msgs)).map_err(|e| e.to_string())?;
    if handle_error(out, st, &body) {
        return Ok(());
    }
    let (r1, s1) = parse_sse(&body, &mut u);
    print_reply(out, &r1, &s1);

    let mut asst = Vec::new();
    let mut results = Vec::new();
    for b in &r1 {
        match b.ty.as_str() {
            "thinking" => asst.push(obj(vec![
                ("type", J::Str("thinking".into())),
                ("thinking", J::Str(b.text.clone())),
                ("signature", J::Str(b.sig.clone())),
            ])),
            "text" => asst.push(obj(vec![("type", J::Str("text".into())), ("text", J::Str(b.text.clone()))])),
            "tool_use" => {
                let input = if b.json.trim().is_empty() { J::Obj(vec![]) } else { parse(&b.json)? };
                asst.push(obj(vec![
                    ("type", J::Str("tool_use".into())),
                    ("id", J::Str(b.id.clone())),
                    ("name", J::Str(b.name.clone())),
                    ("input", input.clone()),
                ]));
                let (ok, res) = run_tool(&b.name, &input);
                out.push_str(&format!("{} {} {}\n", if ok { "result" } else { "error" }, esc(&b.id), esc(&res)));
                let mut tr = vec![
                    ("type", J::Str("tool_result".into())),
                    ("tool_use_id", J::Str(b.id.clone())),
                    ("content", J::Str(res)),
                ];
                if !ok {
                    tr.push(("is_error", J::Bool(true)));
                }
                results.push(obj(tr));
            }
            _ => {}
        }
    }
    out.push_str("request 2\n");
    msgs.push(obj(vec![("role", J::Str("assistant".into())), ("content", J::Arr(asst))]));
    msgs.push(obj(vec![("role", J::Str("user".into())), ("content", J::Arr(results))]));
    let (st, body) = post(&ctx, &make_body(&req, &msgs)).map_err(|e| e.to_string())?;
    if handle_error(out, st, &body) {
        return Ok(());
    }
    let (r2, s2) = parse_sse(&body, &mut u);
    print_reply(out, &r2, &s2);
    out.push_str(&format!("usage input={} output={} cache_read={}\n", u.i, u.o, u.c));
    Ok(())
}

fn main() {
    let mut out = String::new();
    if let Err(e) = run(&mut out) {
        eprintln!("{}", e);
    }
    let so = std::io::stdout();
    let mut l = so.lock();
    let _ = l.write_all(out.as_bytes());
    let _ = l.flush();
}
