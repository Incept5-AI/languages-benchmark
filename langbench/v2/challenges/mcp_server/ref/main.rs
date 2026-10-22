use std::collections::BTreeMap;
use std::io::{Read, Write};

#[derive(Clone, Debug)]
enum V {
    Null,
    Bool(bool),
    Num(String, bool),
    Str(Vec<u8>),
    Arr(Vec<V>),
    Obj(Vec<(Vec<u8>, V)>),
    Raw(String),
}

fn get<'a>(o: &'a [(Vec<u8>, V)], k: &str) -> Option<&'a V> {
    o.iter().find(|(kk, _)| kk.as_slice() == k.as_bytes()).map(|(_, v)| v)
}

struct P<'a> { s: &'a [u8], p: usize }

fn push_cp(b: &mut Vec<u8>, cp: u32) {
    if cp < 0x80 { b.push(cp as u8); }
    else if cp < 0x800 { b.push(0xC0 | (cp >> 6) as u8); b.push(0x80 | (cp & 0x3F) as u8); }
    else if cp < 0x10000 { b.push(0xE0 | (cp >> 12) as u8); b.push(0x80 | ((cp >> 6) & 0x3F) as u8); b.push(0x80 | (cp & 0x3F) as u8); }
    else { b.push(0xF0 | (cp >> 18) as u8); b.push(0x80 | ((cp >> 12) & 0x3F) as u8); b.push(0x80 | ((cp >> 6) & 0x3F) as u8); b.push(0x80 | (cp & 0x3F) as u8); }
}

// decode generalized utf-8 (surrogates allowed) into code points
fn cps(b: &[u8]) -> Vec<u32> {
    let mut v = Vec::new();
    let mut i = 0;
    while i < b.len() {
        let c = b[i] as u32;
        if c < 0x80 { v.push(c); i += 1; }
        else if c >> 5 == 6 && i + 1 < b.len() { v.push(((c & 0x1F) << 6) | (b[i + 1] as u32 & 0x3F)); i += 2; }
        else if c >> 4 == 14 && i + 2 < b.len() { v.push(((c & 0x0F) << 12) | ((b[i + 1] as u32 & 0x3F) << 6) | (b[i + 2] as u32 & 0x3F)); i += 3; }
        else if i + 3 < b.len() { v.push(((c & 0x07) << 18) | ((b[i + 1] as u32 & 0x3F) << 12) | ((b[i + 2] as u32 & 0x3F) << 6) | (b[i + 3] as u32 & 0x3F)); i += 4; }
        else { v.push(0xFFFD); i += 1; }
    }
    v
}

impl<'a> P<'a> {
    fn ws(&mut self) { while self.p < self.s.len() && matches!(self.s[self.p], b' ' | b'\t' | b'\n' | b'\r') { self.p += 1; } }
    fn peek(&self) -> Option<u8> { self.s.get(self.p).copied() }
    fn value(&mut self) -> Result<V, ()> {
        match self.peek() {
            None => Err(()),
            Some(b'{') => self.obj(),
            Some(b'[') => self.arr(),
            Some(b'"') => Ok(V::Str(self.string()?)),
            Some(b't') => { self.lit(b"true")?; Ok(V::Bool(true)) }
            Some(b'f') => { self.lit(b"false")?; Ok(V::Bool(false)) }
            Some(b'n') => { self.lit(b"null")?; Ok(V::Null) }
            Some(c) if c == b'-' || c.is_ascii_digit() => self.num(),
            _ => Err(()),
        }
    }
    fn lit(&mut self, w: &[u8]) -> Result<(), ()> {
        if self.s[self.p..].starts_with(w) { self.p += w.len(); Ok(()) } else { Err(()) }
    }
    fn obj(&mut self) -> Result<V, ()> {
        self.p += 1;
        let mut m: Vec<(Vec<u8>, V)> = Vec::new();
        self.ws();
        if self.peek() == Some(b'}') { self.p += 1; return Ok(V::Obj(m)); }
        loop {
            self.ws();
            if self.peek() != Some(b'"') { return Err(()); }
            let k = self.string()?;
            self.ws();
            if self.peek() != Some(b':') { return Err(()); }
            self.p += 1; self.ws();
            let v = self.value()?;
            m.retain(|(kk, _)| *kk != k);
            m.push((k, v));
            self.ws();
            match self.peek() { Some(b'}') => { self.p += 1; return Ok(V::Obj(m)); } Some(b',') => { self.p += 1; } _ => return Err(()) }
        }
    }
    fn arr(&mut self) -> Result<V, ()> {
        self.p += 1;
        let mut a = Vec::new();
        self.ws();
        if self.peek() == Some(b']') { self.p += 1; return Ok(V::Arr(a)); }
        loop {
            self.ws();
            a.push(self.value()?);
            self.ws();
            match self.peek() { Some(b']') => { self.p += 1; return Ok(V::Arr(a)); } Some(b',') => { self.p += 1; } _ => return Err(()) }
        }
    }
    fn hex4(&mut self) -> Result<u32, ()> {
        if self.p + 4 > self.s.len() { return Err(()); }
        let mut v = 0u32;
        for _ in 0..4 {
            let c = self.s[self.p]; self.p += 1;
            let d = match c { b'0'..=b'9' => c - b'0', b'a'..=b'f' => c - b'a' + 10, b'A'..=b'F' => c - b'A' + 10, _ => return Err(()) };
            v = v * 16 + d as u32;
        }
        Ok(v)
    }
    fn string(&mut self) -> Result<Vec<u8>, ()> {
        self.p += 1;
        let mut b = Vec::new();
        loop {
            let c = match self.peek() { None => return Err(()), Some(c) => c };
            self.p += 1;
            if c == b'"' { return Ok(b); }
            if c < 0x20 { return Err(()); }
            if c == b'\\' {
                let e = match self.peek() { None => return Err(()), Some(e) => e };
                self.p += 1;
                match e {
                    b'"' => b.push(b'"'), b'\\' => b.push(b'\\'), b'/' => b.push(b'/'),
                    b'b' => b.push(8), b'f' => b.push(12), b'n' => b.push(b'\n'), b'r' => b.push(b'\r'), b't' => b.push(b'\t'),
                    b'u' => {
                        let mut cp = self.hex4()?;
                        if (0xD800..0xDC00).contains(&cp) && self.s[self.p..].starts_with(b"\\u") {
                            let save = self.p;
                            self.p += 2;
                            let lo = self.hex4()?;
                            if (0xDC00..0xE000).contains(&lo) { cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00); } else { self.p = save; }
                        }
                        push_cp(&mut b, cp);
                    }
                    _ => return Err(()),
                }
            } else { b.push(c); }
        }
    }
    fn num(&mut self) -> Result<V, ()> {
        let st = self.p;
        let mut is_int = true;
        if self.peek() == Some(b'-') { self.p += 1; }
        match self.peek() {
            Some(b'0') => self.p += 1,
            Some(c) if c.is_ascii_digit() => { while matches!(self.peek(), Some(c) if c.is_ascii_digit()) { self.p += 1; } }
            _ => return Err(()),
        }
        if self.peek() == Some(b'.') {
            is_int = false; self.p += 1;
            let d = self.p;
            while matches!(self.peek(), Some(c) if c.is_ascii_digit()) { self.p += 1; }
            if self.p == d { return Err(()); }
        }
        if matches!(self.peek(), Some(b'e') | Some(b'E')) {
            is_int = false; self.p += 1;
            if matches!(self.peek(), Some(b'+') | Some(b'-')) { self.p += 1; }
            let d = self.p;
            while matches!(self.peek(), Some(c) if c.is_ascii_digit()) { self.p += 1; }
            if self.p == d { return Err(()); }
        }
        Ok(V::Num(String::from_utf8(self.s[st..self.p].to_vec()).unwrap(), is_int))
    }
}

fn parse(s: &[u8]) -> Result<V, ()> {
    let mut p = P { s, p: 0 };
    p.ws();
    let v = p.value()?;
    p.ws();
    if p.p != s.len() { return Err(()); }
    Ok(v)
}

fn ser_str(o: &mut String, s: &[u8]) {
    o.push('"');
    for cp in cps(s) {
        if cp == 0x22 { o.push_str("\\\""); }
        else if cp == 0x5C { o.push_str("\\\\"); }
        else if cp >= 0x20 && cp < 0x7F { o.push(cp as u8 as char); }
        else if cp < 0x10000 { o.push_str(&format!("\\u{:04x}", cp)); }
        else { let c = cp - 0x10000; o.push_str(&format!("\\u{:04x}\\u{:04x}", 0xD800 + (c >> 10), 0xDC00 + (c & 0x3FF))); }
    }
    o.push('"');
}

fn ser(o: &mut String, v: &V) {
    match v {
        V::Null => o.push_str("null"),
        V::Bool(b) => o.push_str(if *b { "true" } else { "false" }),
        V::Num(r, _) => o.push_str(r),
        V::Raw(r) => o.push_str(r),
        V::Str(s) => ser_str(o, s),
        V::Arr(a) => { o.push('['); for (i, x) in a.iter().enumerate() { if i > 0 { o.push(','); } ser(o, x); } o.push(']'); }
        V::Obj(m) => { o.push('{'); for (i, (k, x)) in m.iter().enumerate() { if i > 0 { o.push(','); } ser_str(o, k); o.push(':'); ser(o, x); } o.push('}'); }
    }
}

const TOOLS: &str = r#"[{"name":"kv_set","title":"Set a value","description":"Store a string value under a key, replacing any previous value.","inputSchema":{"type":"object","properties":{"key":{"type":"string","minLength":1},"value":{"type":"string"}},"required":["key","value"],"additionalProperties":false}},{"name":"kv_get","title":"Get a value","description":"Return the value stored under a key.","inputSchema":{"type":"object","properties":{"key":{"type":"string","minLength":1}},"required":["key"],"additionalProperties":false}},{"name":"kv_list","title":"List keys","description":"List the stored keys that start with a prefix, sorted by Unicode code point.","inputSchema":{"type":"object","properties":{"prefix":{"type":"string"}},"additionalProperties":false},"outputSchema":{"type":"object","properties":{"keys":{"type":"array","items":{"type":"string"}},"count":{"type":"integer"}},"required":["keys","count"]}},{"name":"sum","title":"Sum integers","description":"Add up a non-empty list of integers.","inputSchema":{"type":"object","properties":{"numbers":{"type":"array","items":{"type":"integer"},"minItems":1}},"required":["numbers"],"additionalProperties":false},"outputSchema":{"type":"object","properties":{"sum":{"type":"integer"},"count":{"type":"integer"}},"required":["sum","count"]},"annotations":{"readOnlyHint":true,"idempotentHint":true}},{"name":"text_stats","title":"Text statistics","description":"Count the words, Unicode code points and UTF-8 bytes of a text.","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false},"outputSchema":{"type":"object","properties":{"words":{"type":"integer"},"codepoints":{"type":"integer"},"utf8_bytes":{"type":"integer"}},"required":["words","codepoints","utf8_bytes"]},"annotations":{"readOnlyHint":true}}]"#;

fn s(x: &str) -> V { V::Str(x.as_bytes().to_vec()) }
fn obj(items: Vec<(&str, V)>) -> V { V::Obj(items.into_iter().map(|(k, v)| (k.as_bytes().to_vec(), v)).collect()) }
fn int(n: i64) -> V { V::Num(n.to_string(), true) }

fn error_resp(id: &V, code: i64, msg: &str) -> String {
    let mut o = String::from("{\"jsonrpc\":\"2.0\",\"id\":");
    ser(&mut o, id);
    o.push_str(&format!(",\"error\":{{\"code\":{},\"message\":", code));
    ser_str(&mut o, msg.as_bytes());
    o.push_str("}}");
    o
}
fn result_resp(id: &V, r: &V) -> String {
    let mut o = String::from("{\"jsonrpc\":\"2.0\",\"id\":");
    ser(&mut o, id);
    o.push_str(",\"result\":");
    ser(&mut o, r);
    o.push('}');
    o
}
fn text_result(text: Vec<u8>, is_error: bool) -> V {
    obj(vec![("content", V::Arr(vec![obj(vec![("type", s("text")), ("text", V::Str(text))])])), ("isError", V::Bool(is_error))])
}
fn fail(t: &str) -> V { text_result(t.as_bytes().to_vec(), true) }
fn cat(a: &str, b: &[u8]) -> Vec<u8> { let mut v = a.as_bytes().to_vec(); v.extend_from_slice(b); v }
fn struct_result(sv: V) -> V {
    let mut t = String::new();
    ser(&mut t, &sv);
    obj(vec![("content", V::Arr(vec![obj(vec![("type", s("text")), ("text", s(&t))])])), ("structuredContent", sv), ("isError", V::Bool(false))])
}

struct Server { initialized: bool, kv: BTreeMap<Vec<u8>, Vec<u8>> }

// (name, is_string, required, non_empty)
fn spec(name: &str) -> Option<Vec<(&'static str, bool, bool, bool)>> {
    match name {
        "kv_set" => Some(vec![("key", true, true, true), ("value", true, true, false)]),
        "kv_get" => Some(vec![("key", true, true, true)]),
        "kv_list" => Some(vec![("prefix", true, false, false)]),
        "sum" => Some(vec![("numbers", false, true, true)]),
        "text_stats" => Some(vec![("text", true, true, false)]),
        _ => None,
    }
}

impl Server {
    fn call_tool(&mut self, name: &str, args: Option<&V>) -> V {
        let sp = spec(name).unwrap();
        let empty: Vec<(Vec<u8>, V)> = Vec::new();
        let args: &[(Vec<u8>, V)] = match args { None => &empty, Some(V::Obj(m)) => m, Some(_) => return fail("arguments must be an object") };
        let mut unknown: Option<&Vec<u8>> = None;
        for (k, _) in args {
            if !sp.iter().any(|p| p.0.as_bytes() == k.as_slice()) {
                if unknown.map_or(true, |u| k < u) { unknown = Some(k); }
            }
        }
        if let Some(u) = unknown { return text_result(cat("unknown argument: ", u), true); }
        for (pn, is_str, req, non_empty) in &sp {
            match get(args, pn) {
                None => { if *req { return fail(&format!("missing required argument: {}", pn)); } }
                Some(v) => {
                    if *is_str {
                        match v { V::Str(x) => { if *non_empty && x.is_empty() { return fail(&format!("argument {} must not be empty", pn)); } }
                                  _ => return fail(&format!("argument {} must be a string", pn)) }
                    } else {
                        match v {
                            V::Arr(a) if a.iter().all(|x| matches!(x, V::Num(_, true))) => {
                                if *non_empty && a.is_empty() { return fail(&format!("argument {} must not be empty", pn)); }
                            }
                            _ => return fail(&format!("argument {} must be an array of integers", pn)),
                        }
                    }
                }
            }
        }
        let gs = |k: &str| -> Vec<u8> { match get(args, k) { Some(V::Str(x)) => x.clone(), _ => Vec::new() } };
        match name {
            "kv_set" => {
                let k = gs("key");
                let existed = self.kv.insert(k.clone(), gs("value")).is_some();
                text_result(cat(if existed { "updated " } else { "created " }, &k), false)
            }
            "kv_get" => {
                let k = gs("key");
                match self.kv.get(&k) { Some(v) => text_result(v.clone(), false), None => text_result(cat("key not found: ", &k), true) }
            }
            "kv_list" => {
                let pre = gs("prefix");
                let keys: Vec<V> = self.kv.keys().filter(|k| k.starts_with(&pre)).map(|k| V::Str(k.clone())).collect();
                let n = keys.len() as i64;
                struct_result(obj(vec![("keys", V::Arr(keys)), ("count", int(n))]))
            }
            "sum" => {
                let a = match get(args, "numbers") { Some(V::Arr(a)) => a, _ => unreachable!() };
                let mut sum: i64 = 0;
                for x in a { if let V::Num(r, _) = x { sum = sum.wrapping_add(r.parse::<i64>().unwrap_or(0)); } }
                struct_result(obj(vec![("sum", int(sum)), ("count", int(a.len() as i64))]))
            }
            _ => {
                let t = gs("text");
                let (mut words, mut n, mut bytes, mut inw) = (0i64, 0i64, 0i64, false);
                for cp in cps(&t) {
                    n += 1;
                    bytes += if cp < 0x80 { 1 } else if cp < 0x800 { 2 } else if cp < 0x10000 { 3 } else { 4 };
                    let sep = cp == 32 || cp == 9 || cp == 10 || cp == 13;
                    if !sep && !inw { words += 1; }
                    inw = !sep;
                }
                struct_result(obj(vec![("words", int(words)), ("codepoints", int(n)), ("utf8_bytes", int(bytes))]))
            }
        }
    }

    fn handle(&mut self, line: &[u8]) -> Option<String> {
        let msg = match parse(line) { Ok(v) => v, Err(_) => return Some(error_resp(&V::Null, -32700, "Parse error")) };
        let m = match &msg { V::Obj(m) => m, _ => return Some(error_resp(&V::Null, -32600, "Invalid Request")) };
        if get(m, "method").is_none() && (get(m, "result").is_some() || get(m, "error").is_some()) { return None; }
        let id = get(m, "id");
        let id_ok = matches!(id, Some(V::Str(_)) | Some(V::Num(_, true)));
        let jsonrpc_ok = matches!(get(m, "jsonrpc"), Some(V::Str(x)) if x.as_slice() == b"2.0");
        let method = match get(m, "method") { Some(V::Str(x)) => Some(x.clone()), _ => None };
        if !jsonrpc_ok || method.is_none() || (id.is_some() && !id_ok) {
            return Some(error_resp(if id_ok { id.unwrap() } else { &V::Null }, -32600, "Invalid Request"));
        }
        let id = match id { None => return None, Some(i) => i };
        let method = method.unwrap();
        let method: &[u8] = &method;
        if !(method == b"initialize" || method == b"ping" || method == b"tools/list" || method == b"tools/call") {
            return Some(error_resp(id, -32601, "Method not found"));
        }
        let pm: Option<&[(Vec<u8>, V)]> = match get(m, "params") { None => None, Some(V::Obj(p)) => Some(p), Some(_) => return Some(error_resp(id, -32602, "Invalid params")) };
        if !self.initialized && method != b"initialize" && method != b"ping" { return Some(error_resp(id, -32002, "Server not initialized")); }
        match method {
            b"initialize" => {
                if self.initialized { return Some(error_resp(id, -32600, "Already initialized")); }
                let v = match pm.and_then(|p| get(p, "protocolVersion")) { Some(V::Str(x)) => x.clone(), _ => return Some(error_resp(id, -32602, "Invalid params")) };
                let v: &[u8] = if v.as_slice() == b"2025-06-18" || v.as_slice() == b"2025-03-26" || v.as_slice() == b"2024-11-05" { &v } else { b"2025-06-18" };
                self.initialized = true;
                let mut r = String::from("{\"protocolVersion\":");
                ser_str(&mut r, v);
                r.push_str(",\"capabilities\":{\"tools\":{\"listChanged\":false}},\"serverInfo\":{\"name\":\"cb2-kv\",\"title\":\"CB2 key-value server\",\"version\":\"1.4.2\"}}");
                Some(result_resp(id, &V::Raw(r)))
            }
            b"ping" => Some(result_resp(id, &V::Raw("{}".into()))),
            b"tools/list" => Some(result_resp(id, &V::Raw(format!("{{\"tools\":{}}}", TOOLS)))),
            _ => {
                let pm = match pm { Some(p) => p, None => return Some(error_resp(id, -32602, "Invalid params")) };
                let name = match get(pm, "name") { Some(V::Str(x)) => x.clone(), _ => return Some(error_resp(id, -32602, "Invalid params")) };
                let ns = String::from_utf8_lossy(&name).to_string();
                if spec(&ns).is_none() {
                    let mut o = String::from("{\"jsonrpc\":\"2.0\",\"id\":");
                    ser(&mut o, id);
                    o.push_str(",\"error\":{\"code\":-32602,\"message\":");
                    ser_str(&mut o, &cat("Unknown tool: ", &name));
                    o.push_str("}}");
                    return Some(o);
                }
                let r = self.call_tool(&ns, get(pm, "arguments"));
                Some(result_resp(id, &r))
            }
        }
    }
}

fn run() {
    let mut data = Vec::new();
    std::io::stdin().read_to_end(&mut data).unwrap();
    let mut srv = Server { initialized: false, kv: BTreeMap::new() };
    let mut out = String::new();
    let parts: Vec<&[u8]> = data.split(|&b| b == b'\n').collect();
    let n = parts.len();
    for (i, raw) in parts.into_iter().enumerate() {
        if i == n - 1 && raw.is_empty() { break; }
        if raw.iter().all(|&c| c == b' ' || c == b'\t' || c == b'\r') { continue; }
        let line = String::from_utf8_lossy(raw);
        if let Some(r) = srv.handle(line.as_bytes()) { out.push_str(&r); out.push('\n'); }
    }
    let so = std::io::stdout();
    let mut lk = so.lock();
    lk.write_all(out.as_bytes()).unwrap();
    lk.flush().unwrap();
}

fn main() {
    let h = std::thread::Builder::new().stack_size(1 << 30).spawn(run).unwrap();
    h.join().unwrap();
}
