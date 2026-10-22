use std::collections::{BTreeMap, HashMap};
use std::io::Write;

#[derive(Clone, Copy, PartialEq, Debug)]
enum Sc {
    Int32,
    Int64,
    Uint32,
    Uint64,
    Sint32,
    Sint64,
    Bool,
    Str,
    Bytes,
    Fixed32,
    Fixed64,
    Sfixed32,
    Sfixed64,
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum Ty {
    S(Sc),
    M(usize),
    E(usize),
}

#[derive(Clone, Copy, PartialEq)]
enum Label {
    Single,
    Optional,
    Repeated,
    Map,
}

struct Field {
    json: String,
    num: u64,
    type_name: String,
    key_name: String,
    ty: Ty,
    key_ty: Ty,
    label: Label,
    oneof: i32,
}

struct MsgDesc {
    name: String,
    fields: Vec<Field>,
    by_num: HashMap<u64, usize>,
    oneof_count: usize,
}

struct EnumDesc {
    name: String,
    by_num: HashMap<i64, String>,
}

struct Schema {
    msgs: Vec<MsgDesc>,
    enums: Vec<EnumDesc>,
    root: usize,
}

fn scalar_of(s: &str) -> Option<Sc> {
    Some(match s {
        "int32" => Sc::Int32,
        "int64" => Sc::Int64,
        "uint32" => Sc::Uint32,
        "uint64" => Sc::Uint64,
        "sint32" => Sc::Sint32,
        "sint64" => Sc::Sint64,
        "bool" => Sc::Bool,
        "string" => Sc::Str,
        "bytes" => Sc::Bytes,
        "fixed32" => Sc::Fixed32,
        "fixed64" => Sc::Fixed64,
        "sfixed32" => Sc::Sfixed32,
        "sfixed64" => Sc::Sfixed64,
        _ => return None,
    })
}

fn is_word(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_' || c == b'.' || c == b'+' || c == b'-'
}

fn tokenize(src: &[u8]) -> Vec<String> {
    let mut toks = Vec::new();
    let n = src.len();
    let mut i = 0;
    while i < n {
        let c = src[i];
        if c.is_ascii_whitespace() || c == 0x0b {
            i += 1;
            continue;
        }
        if c == b'/' && i + 1 < n && src[i + 1] == b'/' {
            while i < n && src[i] != b'\n' {
                i += 1;
            }
            continue;
        }
        if c == b'/' && i + 1 < n && src[i + 1] == b'*' {
            let mut j = i + 2;
            while j + 1 < n && !(src[j] == b'*' && src[j + 1] == b'/') {
                j += 1;
            }
            i = if j + 1 < n { j + 2 } else { n };
            continue;
        }
        if c == b'"' || c == b'\'' {
            let mut j = i + 1;
            while j < n && src[j] != c {
                if src[j] == b'\\' {
                    j += 1;
                }
                j += 1;
            }
            let e = (j + 1).min(n);
            toks.push(String::from_utf8_lossy(&src[i..e]).into_owned());
            i = e;
            continue;
        }
        if is_word(c) {
            let mut j = i;
            while j < n && is_word(src[j]) {
                j += 1;
            }
            toks.push(String::from_utf8_lossy(&src[i..j]).into_owned());
            i = j;
            continue;
        }
        toks.push((c as char).to_string());
        i += 1;
    }
    toks
}

fn camel(name: &str) -> String {
    let mut out = String::new();
    let mut up = false;
    for ch in name.chars() {
        if ch == '_' {
            up = true;
            continue;
        }
        if up && ch.is_ascii_lowercase() {
            out.push(ch.to_ascii_uppercase());
        } else {
            out.push(ch);
        }
        up = false;
    }
    out
}

fn parse_int(s: &str) -> i64 {
    let (neg, s) = if let Some(r) = s.strip_prefix('-') {
        (true, r)
    } else if let Some(r) = s.strip_prefix('+') {
        (false, r)
    } else {
        (false, s)
    };
    let v = if s.starts_with("0x") || s.starts_with("0X") {
        i64::from_str_radix(&s[2..], 16).unwrap_or(0)
    } else if s.len() > 1 && s.starts_with('0') {
        i64::from_str_radix(&s[1..], 8).unwrap_or(0)
    } else {
        s.parse::<i64>().unwrap_or(0)
    };
    if neg {
        -v
    } else {
        v
    }
}

struct Parser {
    toks: Vec<String>,
    pos: usize,
    msgs: Vec<MsgDesc>,
    enums: Vec<EnumDesc>,
    root: Option<usize>,
}

impl Parser {
    fn peek(&self) -> &str {
        if self.pos < self.toks.len() {
            &self.toks[self.pos]
        } else {
            ""
        }
    }
    fn next(&mut self) -> String {
        if self.pos < self.toks.len() {
            self.pos += 1;
            self.toks[self.pos - 1].clone()
        } else {
            String::new()
        }
    }
    fn more(&self) -> bool {
        self.pos < self.toks.len()
    }
    fn skip_statement(&mut self) {
        let mut depth = 0i32;
        while self.more() {
            let t = self.next();
            match t.as_str() {
                "{" | "[" | "(" => depth += 1,
                "}" | "]" | ")" => depth -= 1,
                ";" if depth <= 0 => return,
                _ => {}
            }
        }
    }
    fn skip_field_options(&mut self) {
        if self.peek() == "[" {
            let mut depth = 0;
            while self.more() {
                let t = self.next();
                if t == "[" {
                    depth += 1;
                } else if t == "]" {
                    depth -= 1;
                    if depth == 0 {
                        break;
                    }
                }
            }
        }
    }
    fn parse_enum(&mut self) {
        let name = self.next();
        let mut ed = EnumDesc { name, by_num: HashMap::new() };
        self.next(); // {
        while self.more() && self.peek() != "}" {
            let t = self.peek().to_string();
            if t == ";" {
                self.next();
                continue;
            }
            if t == "option" || t == "reserved" {
                self.skip_statement();
                continue;
            }
            let vname = self.next();
            self.next(); // =
            let num = parse_int(&self.next());
            self.skip_field_options();
            self.next(); // ;
            ed.by_num.entry(num).or_insert(vname);
        }
        self.next(); // }
        self.enums.push(ed);
    }
    fn parse_field(&mut self, md: &mut MsgDesc, oneof: i32) {
        let mut label = Label::Single;
        let mut t = self.next();
        if t == "repeated" {
            label = Label::Repeated;
            t = self.next();
        } else if t == "optional" {
            label = Label::Optional;
            t = self.next();
        }
        let mut key_name = String::new();
        if t == "map" && self.peek() == "<" {
            self.next();
            key_name = self.next();
            self.next(); // ,
            t = self.next();
            self.next(); // >
            label = Label::Map;
        }
        let name = self.next();
        self.next(); // =
        let num = parse_int(&self.next()) as u64;
        self.skip_field_options();
        self.next(); // ;
        md.by_num.insert(num, md.fields.len());
        md.fields.push(Field {
            json: camel(&name),
            num,
            type_name: t,
            key_name,
            ty: Ty::S(Sc::Int32),
            key_ty: Ty::S(Sc::Int32),
            label,
            oneof,
        });
    }
    fn parse_message(&mut self) -> usize {
        let name = self.next();
        let mut md = MsgDesc { name, fields: Vec::new(), by_num: HashMap::new(), oneof_count: 0 };
        self.next(); // {
        let idx = self.msgs.len();
        self.msgs.push(MsgDesc { name: String::new(), fields: vec![], by_num: HashMap::new(), oneof_count: 0 });
        while self.more() && self.peek() != "}" {
            let t = self.peek().to_string();
            match t.as_str() {
                ";" => {
                    self.next();
                }
                "message" => {
                    self.next();
                    self.parse_message();
                }
                "enum" => {
                    self.next();
                    self.parse_enum();
                }
                "option" | "reserved" | "extensions" => self.skip_statement(),
                "oneof" => {
                    self.next();
                    self.next();
                    let oi = md.oneof_count as i32;
                    md.oneof_count += 1;
                    self.next(); // {
                    while self.more() && self.peek() != "}" {
                        let u = self.peek().to_string();
                        if u == ";" {
                            self.next();
                            continue;
                        }
                        if u == "option" {
                            self.skip_statement();
                            continue;
                        }
                        self.parse_field(&mut md, oi);
                    }
                    self.next();
                }
                _ => self.parse_field(&mut md, -1),
            }
        }
        self.next(); // }
        md.fields.sort_by_key(|f| f.num);
        md.by_num.clear();
        for (i, f) in md.fields.iter().enumerate() {
            md.by_num.insert(f.num, i);
        }
        self.msgs[idx] = md;
        idx
    }
    fn parse(&mut self) {
        while self.more() {
            let t = self.peek().to_string();
            if t == "message" {
                self.next();
                let i = self.parse_message();
                if self.root.is_none() {
                    self.root = Some(i);
                }
            } else if t == "enum" {
                self.next();
                self.parse_enum();
            } else if t == ";" {
                self.next();
            } else {
                self.skip_statement();
            }
        }
    }
}

fn build_schema(src: &[u8]) -> Schema {
    let mut p = Parser { toks: tokenize(src), pos: 0, msgs: vec![], enums: vec![], root: None };
    p.parse();
    let mut names: HashMap<String, Ty> = HashMap::new();
    for (i, m) in p.msgs.iter().enumerate() {
        names.insert(m.name.clone(), Ty::M(i));
    }
    for (i, e) in p.enums.iter().enumerate() {
        names.insert(e.name.clone(), Ty::E(i));
    }
    let resolve = |s: &str| -> Ty {
        if let Some(sc) = scalar_of(s) {
            Ty::S(sc)
        } else {
            *names.get(s).unwrap_or(&Ty::S(Sc::Bytes))
        }
    };
    for m in p.msgs.iter_mut() {
        for f in m.fields.iter_mut() {
            f.ty = resolve(&f.type_name);
            if f.label == Label::Map {
                f.key_ty = resolve(&f.key_name);
            }
        }
    }
    Schema { msgs: p.msgs, enums: p.enums, root: p.root.unwrap_or(0) }
}

// ---------- values ----------
#[derive(Clone)]
enum Val {
    N(u64),
    B(Vec<u8>),
    M(MsgVal),
}

#[derive(Clone, PartialEq, Eq, PartialOrd, Ord)]
enum MapKey {
    I(i64),
    S(Vec<u8>),
}

#[derive(Clone)]
enum Slot {
    One(Val),
    Rep(Vec<Val>),
    Map(BTreeMap<MapKey, Val>),
}

#[derive(Clone)]
struct MsgVal {
    fields: HashMap<u64, Slot>,
    oneof: Vec<u64>,
}

fn new_msg(s: &Schema, mi: usize) -> MsgVal {
    MsgVal { fields: HashMap::new(), oneof: vec![0; s.msgs[mi].oneof_count] }
}

struct Reader<'a> {
    buf: &'a [u8],
    pos: usize,
    end: usize,
}

impl<'a> Reader<'a> {
    fn varint(&mut self) -> Option<u64> {
        let mut r: u64 = 0;
        let mut shift = 0u32;
        loop {
            if self.pos >= self.end {
                return None;
            }
            let b = self.buf[self.pos];
            self.pos += 1;
            if shift < 64 {
                r |= ((b & 0x7f) as u64) << shift;
            }
            shift += 7;
            if b & 0x80 == 0 {
                break;
            }
        }
        Some(r)
    }
    fn fixed(&mut self, n: usize) -> Option<u64> {
        if self.pos + n > self.end {
            return None;
        }
        let mut v = 0u64;
        for i in 0..n {
            v |= (self.buf[self.pos + i] as u64) << (8 * i);
        }
        self.pos += n;
        Some(v)
    }
    fn len_delim(&mut self) -> Option<(usize, usize)> {
        let l = self.varint()? as usize;
        if l > self.end - self.pos {
            return None;
        }
        let s = self.pos;
        self.pos += l;
        Some((s, s + l))
    }
}

fn skip_field(r: &mut Reader, wt: u64, fnum: u64) -> Option<()> {
    match wt {
        0 => {
            r.varint()?;
        }
        1 => {
            r.fixed(8)?;
        }
        5 => {
            r.fixed(4)?;
        }
        2 => {
            r.len_delim()?;
        }
        3 => loop {
            let key = r.varint()?;
            let w = key & 7;
            let n = key >> 3;
            if w == 4 {
                if n != fnum {
                    return None;
                }
                break;
            }
            skip_field(r, w, n)?;
        },
        4 => {}
        _ => return None,
    }
    Some(())
}

fn wire_of(t: Ty) -> u64 {
    match t {
        Ty::S(Sc::Fixed32) | Ty::S(Sc::Sfixed32) => 5,
        Ty::S(Sc::Fixed64) | Ty::S(Sc::Sfixed64) => 1,
        Ty::S(Sc::Str) | Ty::S(Sc::Bytes) | Ty::M(_) => 2,
        _ => 0,
    }
}

fn read_scalar(r: &mut Reader, wt: u64) -> Option<Val> {
    match wt {
        0 => Some(Val::N(r.varint()?)),
        5 => Some(Val::N(r.fixed(4)?)),
        1 => Some(Val::N(r.fixed(8)?)),
        _ => {
            let (a, b) = r.len_delim()?;
            Some(Val::B(r.buf[a..b].to_vec()))
        }
    }
}

fn default_val(s: &Schema, t: Ty) -> Val {
    match t {
        Ty::M(mi) => Val::M(new_msg(s, mi)),
        Ty::S(Sc::Str) | Ty::S(Sc::Bytes) => Val::B(Vec::new()),
        _ => Val::N(0),
    }
}

fn conv_int(t: Ty, v: u64) -> i128 {
    match t {
        Ty::S(Sc::Int32) | Ty::S(Sc::Sfixed32) | Ty::E(_) => (v as u32 as i32) as i128,
        Ty::S(Sc::Uint32) | Ty::S(Sc::Fixed32) => (v as u32) as i128,
        Ty::S(Sc::Sint32) => {
            let u = v as u32;
            (((u >> 1) as i32) ^ (-((u & 1) as i32))) as i128
        }
        Ty::S(Sc::Int64) | Ty::S(Sc::Sfixed64) => (v as i64) as i128,
        Ty::S(Sc::Uint64) | Ty::S(Sc::Fixed64) => v as i128,
        Ty::S(Sc::Sint64) => (((v >> 1) as i64) ^ (-((v & 1) as i64))) as i128,
        Ty::S(Sc::Bool) => (v != 0) as i128,
        _ => v as i128,
    }
}

fn decode_into(s: &Schema, mi: usize, msg: &mut MsgVal, buf: &[u8], start: usize, end: usize) -> Option<()> {
    let md = &s.msgs[mi];
    let mut r = Reader { buf, pos: start, end };
    while r.pos < r.end {
        let key = r.varint()?;
        let wt = key & 7;
        let fnum = key >> 3;
        let fi = match md.by_num.get(&fnum) {
            Some(&i) => i,
            None => {
                skip_field(&mut r, wt, fnum)?;
                continue;
            }
        };
        let f = &md.fields[fi];
        if f.label == Label::Map {
            if wt != 2 {
                skip_field(&mut r, wt, fnum)?;
                continue;
            }
            let (a, b) = r.len_delim()?;
            // decode entry
            let mut kv: Option<Val> = None;
            let mut vv: Option<Val> = None;
            let mut er = Reader { buf, pos: a, end: b };
            while er.pos < er.end {
                let k2 = er.varint()?;
                let w2 = k2 & 7;
                let n2 = k2 >> 3;
                let t = if n2 == 1 { Some(f.key_ty) } else if n2 == 2 { Some(f.ty) } else { None };
                match t {
                    Some(t) if wire_of(t) == w2 => {
                        if let Ty::M(sub) = t {
                            let (c, d) = er.len_delim()?;
                            let mut m = match vv.take() {
                                Some(Val::M(m)) => m,
                                _ => new_msg(s, sub),
                            };
                            decode_into(s, sub, &mut m, buf, c, d)?;
                            vv = Some(Val::M(m));
                        } else {
                            let v = read_scalar(&mut er, w2)?;
                            if n2 == 1 {
                                kv = Some(v);
                            } else {
                                vv = Some(v);
                            }
                        }
                    }
                    _ => {
                        skip_field(&mut er, w2, n2)?;
                    }
                }
            }
            let kv = kv.unwrap_or_else(|| default_val(s, f.key_ty));
            let vv = vv.unwrap_or_else(|| default_val(s, f.ty));
            let mk = match kv {
                Val::B(b) => MapKey::S(b),
                Val::N(n) => MapKey::I(conv_int(f.key_ty, n) as i64),
                _ => MapKey::I(0),
            };
            let slot = msg.fields.entry(fnum).or_insert_with(|| Slot::Map(BTreeMap::new()));
            if let Slot::Map(m) = slot {
                m.insert(mk, vv);
            }
            continue;
        }
        let ewt = wire_of(f.ty);
        if f.label == Label::Repeated {
            if wt == 2 && ewt != 2 {
                let (a, b) = r.len_delim()?;
                let slot = msg.fields.entry(fnum).or_insert_with(|| Slot::Rep(Vec::new()));
                let mut sr = Reader { buf, pos: a, end: b };
                if let Slot::Rep(v) = slot {
                    while sr.pos < sr.end {
                        v.push(read_scalar(&mut sr, ewt)?);
                    }
                }
                continue;
            }
            if wt != ewt {
                skip_field(&mut r, wt, fnum)?;
                continue;
            }
            let val = if let Ty::M(sub) = f.ty {
                let (a, b) = r.len_delim()?;
                let mut m = new_msg(s, sub);
                decode_into(s, sub, &mut m, buf, a, b)?;
                Val::M(m)
            } else {
                read_scalar(&mut r, wt)?
            };
            let slot = msg.fields.entry(fnum).or_insert_with(|| Slot::Rep(Vec::new()));
            if let Slot::Rep(v) = slot {
                v.push(val);
            }
            continue;
        }
        if wt != ewt {
            skip_field(&mut r, wt, fnum)?;
            continue;
        }
        if f.oneof >= 0 {
            let o = f.oneof as usize;
            let cur = msg.oneof[o];
            if cur != 0 && cur != fnum {
                msg.fields.remove(&cur);
            }
            msg.oneof[o] = fnum;
        }
        if let Ty::M(sub) = f.ty {
            let (a, b) = r.len_delim()?;
            let mut m = match msg.fields.remove(&fnum) {
                Some(Slot::One(Val::M(m))) => m,
                _ => new_msg(s, sub),
            };
            decode_into(s, sub, &mut m, buf, a, b)?;
            msg.fields.insert(fnum, Slot::One(Val::M(m)));
        } else {
            let v = read_scalar(&mut r, wt)?;
            msg.fields.insert(fnum, Slot::One(v));
        }
    }
    Some(())
}

// ---------- output ----------
fn esc(out: &mut Vec<u8>, b: &[u8]) {
    out.push(b'"');
    for &c in b {
        match c {
            b'"' => out.extend_from_slice(b"\\\""),
            b'\\' => out.extend_from_slice(b"\\\\"),
            8 => out.extend_from_slice(b"\\b"),
            9 => out.extend_from_slice(b"\\t"),
            10 => out.extend_from_slice(b"\\n"),
            12 => out.extend_from_slice(b"\\f"),
            13 => out.extend_from_slice(b"\\r"),
            c if c < 0x20 => out.extend_from_slice(format!("\\u{:04x}", c).as_bytes()),
            c => out.push(c),
        }
    }
    out.push(b'"');
}

fn base64(b: &[u8]) -> String {
    const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut s = String::new();
    for ch in b.chunks(3) {
        let n = (ch[0] as u32) << 16 | (*ch.get(1).unwrap_or(&0) as u32) << 8 | *ch.get(2).unwrap_or(&0) as u32;
        s.push(A[(n >> 18) as usize & 63] as char);
        s.push(A[(n >> 12) as usize & 63] as char);
        s.push(if ch.len() > 1 { A[(n >> 6) as usize & 63] as char } else { '=' });
        s.push(if ch.len() > 2 { A[n as usize & 63] as char } else { '=' });
    }
    s
}

fn is_default(t: Ty, v: &Val) -> bool {
    match v {
        Val::B(b) => b.is_empty(),
        Val::N(n) => conv_int(t, *n) == 0,
        Val::M(_) => false,
    }
}

fn fmt_val(s: &Schema, t: Ty, v: &Val, out: &mut Vec<u8>) {
    match (t, v) {
        (Ty::M(mi), Val::M(m)) => fmt_msg(s, mi, m, out),
        (Ty::S(Sc::Str), Val::B(b)) => esc(out, b),
        (Ty::S(Sc::Bytes), Val::B(b)) => {
            out.push(b'"');
            out.extend_from_slice(base64(b).as_bytes());
            out.push(b'"');
        }
        (Ty::S(Sc::Bool), Val::N(n)) => out.extend_from_slice(if *n != 0 { b"true" } else { b"false" }),
        (Ty::S(Sc::Int64), Val::N(n))
        | (Ty::S(Sc::Uint64), Val::N(n))
        | (Ty::S(Sc::Sint64), Val::N(n))
        | (Ty::S(Sc::Fixed64), Val::N(n))
        | (Ty::S(Sc::Sfixed64), Val::N(n)) => {
            out.extend_from_slice(format!("\"{}\"", conv_int(t, *n)).as_bytes());
        }
        (Ty::E(ei), Val::N(n)) => {
            let x = conv_int(t, *n) as i64;
            match s.enums[ei].by_num.get(&x) {
                Some(name) => esc(out, name.as_bytes()),
                None => out.extend_from_slice(x.to_string().as_bytes()),
            }
        }
        (_, Val::N(n)) => out.extend_from_slice(conv_int(t, *n).to_string().as_bytes()),
        _ => out.extend_from_slice(b"null"),
    }
}

fn fmt_msg(s: &Schema, mi: usize, m: &MsgVal, out: &mut Vec<u8>) {
    out.push(b'{');
    let mut first = true;
    for f in &s.msgs[mi].fields {
        let slot = match m.fields.get(&f.num) {
            Some(x) => x,
            None => continue,
        };
        let skip = match slot {
            Slot::Map(mm) => mm.is_empty(),
            Slot::Rep(v) => v.is_empty(),
            Slot::One(v) => !matches!(f.ty, Ty::M(_)) && f.label != Label::Optional && f.oneof < 0 && is_default(f.ty, v),
        };
        if skip {
            continue;
        }
        if !first {
            out.push(b',');
        }
        first = false;
        esc(out, f.json.as_bytes());
        out.push(b':');
        match slot {
            Slot::Map(mm) => {
                out.push(b'{');
                let mut fst = true;
                for (k, v) in mm {
                    if !fst {
                        out.push(b',');
                    }
                    fst = false;
                    match k {
                        MapKey::S(b) => esc(out, b),
                        MapKey::I(i) => out.extend_from_slice(format!("\"{}\"", i).as_bytes()),
                    }
                    out.push(b':');
                    fmt_val(s, f.ty, v, out);
                }
                out.push(b'}');
            }
            Slot::Rep(v) => {
                out.push(b'[');
                for (i, e) in v.iter().enumerate() {
                    if i > 0 {
                        out.push(b',');
                    }
                    fmt_val(s, f.ty, e, out);
                }
                out.push(b']');
            }
            Slot::One(v) => fmt_val(s, f.ty, v, out),
        }
    }
    out.push(b'}');
}

fn main() {
    let dir = std::env::args().nth(1).unwrap_or(".".into());
    let src = std::fs::read(format!("{}/schema.proto", dir)).unwrap();
    let data = std::fs::read(format!("{}/messages.bin", dir)).unwrap();
    let s = build_schema(&src);
    let mut out: Vec<u8> = Vec::new();
    let mut r = Reader { buf: &data, pos: 0, end: data.len() };
    while r.pos < r.end {
        let (a, b) = match r.len_delim() {
            Some(x) => x,
            None => break,
        };
        let mut m = new_msg(&s, s.root);
        let _ = decode_into(&s, s.root, &mut m, &data, a, b);
        fmt_msg(&s, s.root, &m, &mut out);
        out.push(b'\n');
    }
    std::io::stdout().write_all(&out).unwrap();
}
