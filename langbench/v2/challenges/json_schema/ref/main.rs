use std::collections::BTreeSet;
use std::fs;
use std::io::Write;
use std::path::Path;

#[derive(Debug, Clone)]
enum V {
    Null,
    Bool(bool),
    Num(f64),
    Str(String),
    Arr(Vec<V>),
    Obj(Vec<(String, V)>),
}

impl V {
    fn get(&self, k: &str) -> Option<&V> {
        if let V::Obj(m) = self {
            for (kk, v) in m {
                if kk == k {
                    return Some(v);
                }
            }
        }
        None
    }
    fn tname(&self) -> &'static str {
        match self {
            V::Null => "null",
            V::Bool(_) => "boolean",
            V::Num(_) => "number",
            V::Str(_) => "string",
            V::Arr(_) => "array",
            V::Obj(_) => "object",
        }
    }
    fn num(&self) -> f64 {
        if let V::Num(n) = self { *n } else { f64::NAN }
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
    fn value(&mut self) -> V {
        self.ws();
        let c = self.s[self.i];
        match c {
            b'{' => {
                self.i += 1;
                let mut m = Vec::new();
                self.ws();
                if self.s[self.i] == b'}' {
                    self.i += 1;
                    return V::Obj(m);
                }
                loop {
                    self.ws();
                    let k = self.string();
                    self.ws();
                    self.i += 1;
                    let v = self.value();
                    m.push((k, v));
                    self.ws();
                    let d = self.s[self.i];
                    self.i += 1;
                    if d == b'}' {
                        break;
                    }
                }
                V::Obj(m)
            }
            b'[' => {
                self.i += 1;
                let mut a = Vec::new();
                self.ws();
                if self.s[self.i] == b']' {
                    self.i += 1;
                    return V::Arr(a);
                }
                loop {
                    a.push(self.value());
                    self.ws();
                    let d = self.s[self.i];
                    self.i += 1;
                    if d == b']' {
                        break;
                    }
                }
                V::Arr(a)
            }
            b'"' => V::Str(self.string()),
            b't' => {
                self.i += 4;
                V::Bool(true)
            }
            b'f' => {
                self.i += 5;
                V::Bool(false)
            }
            b'n' => {
                self.i += 4;
                V::Null
            }
            _ => {
                let st = self.i;
                while self.i < self.s.len() && b"+-0123456789.eE".contains(&self.s[self.i]) {
                    self.i += 1;
                }
                let t = std::str::from_utf8(&self.s[st..self.i]).unwrap();
                V::Num(t.parse::<f64>().unwrap())
            }
        }
    }
    fn hex4(&mut self) -> u32 {
        let t = std::str::from_utf8(&self.s[self.i..self.i + 4]).unwrap();
        self.i += 4;
        u32::from_str_radix(t, 16).unwrap()
    }
    fn string(&mut self) -> String {
        self.i += 1;
        let mut b: Vec<u8> = Vec::new();
        loop {
            let c = self.s[self.i];
            self.i += 1;
            if c == b'"' {
                break;
            }
            if c == b'\\' {
                let e = self.s[self.i];
                self.i += 1;
                match e {
                    b'n' => b.push(b'\n'),
                    b't' => b.push(b'\t'),
                    b'r' => b.push(b'\r'),
                    b'b' => b.push(8),
                    b'f' => b.push(12),
                    b'u' => {
                        let mut cp = self.hex4();
                        if (0xD800..0xDC00).contains(&cp) && self.s[self.i] == b'\\' && self.s[self.i + 1] == b'u' {
                            self.i += 2;
                            let lo = self.hex4();
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        }
                        let ch = char::from_u32(cp).unwrap_or('\u{FFFD}');
                        let mut buf = [0u8; 4];
                        b.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                    }
                    _ => b.push(e),
                }
            } else {
                b.push(c);
            }
        }
        String::from_utf8(b).unwrap()
    }
}

fn deep_eq(a: &V, b: &V) -> bool {
    match (a, b) {
        (V::Null, V::Null) => true,
        (V::Bool(x), V::Bool(y)) => x == y,
        (V::Num(x), V::Num(y)) => x == y,
        (V::Str(x), V::Str(y)) => x == y,
        (V::Arr(x), V::Arr(y)) => x.len() == y.len() && x.iter().zip(y.iter()).all(|(p, q)| deep_eq(p, q)),
        (V::Obj(x), V::Obj(y)) => {
            x.len() == y.len()
                && x.iter().all(|(k, v)| match b.get(k) {
                    Some(w) => deep_eq(v, w),
                    None => false,
                })
        }
        _ => false,
    }
}

fn esc(t: &str) -> String {
    t.replace('~', "~0").replace('/', "~1")
}

type Errs = BTreeSet<(String, String)>;

struct Ctx {
    root: V,
}

impl Ctx {
    fn accepts(&self, inst: &V, l: &str, s: &V, p: &str) -> bool {
        let mut e = Errs::new();
        self.validate(inst, l, s, p, &mut e);
        e.is_empty()
    }

    fn validate(&self, inst: &V, l: &str, s: &V, p: &str, out: &mut Errs) {
        let add = |out: &mut Errs, kw: &str| {
            out.insert((l.to_string(), format!("{}/{}", p, kw)));
        };
        let m = match s {
            V::Bool(b) => {
                if !*b {
                    out.insert((l.to_string(), p.to_string()));
                }
                return;
            }
            V::Obj(_) => s,
            _ => return,
        };
        let it = inst.tname();
        if let Some(t) = m.get("type") {
            let ts: Vec<&V> = match t {
                V::Arr(a) => a.iter().collect(),
                _ => vec![t],
            };
            let mut ok = false;
            for n in ts {
                if let V::Str(n) = n {
                    if n == it {
                        ok = true;
                    } else if n == "integer" {
                        if let V::Num(d) = inst {
                            if d.fract() == 0.0 {
                                ok = true;
                            }
                        }
                    }
                }
            }
            if !ok {
                add(out, "type");
            }
        }
        if let Some(V::Arr(e)) = m.get("enum") {
            if !e.iter().any(|o| deep_eq(inst, o)) {
                add(out, "enum");
            }
        }
        if let Some(c) = m.get("const") {
            if !deep_eq(inst, c) {
                add(out, "const");
            }
        }
        if let V::Num(d) = inst {
            let d = *d;
            if let Some(v) = m.get("minimum") { if !(d >= v.num()) { add(out, "minimum"); } }
            if let Some(v) = m.get("maximum") { if !(d <= v.num()) { add(out, "maximum"); } }
            if let Some(v) = m.get("exclusiveMinimum") { if !(d > v.num()) { add(out, "exclusiveMinimum"); } }
            if let Some(v) = m.get("exclusiveMaximum") { if !(d < v.num()) { add(out, "exclusiveMaximum"); } }
        }
        if let V::Str(st) = inst {
            let len = st.chars().count() as f64;
            if let Some(v) = m.get("minLength") { if !(len >= v.num()) { add(out, "minLength"); } }
            if let Some(v) = m.get("maxLength") { if !(len <= v.num()) { add(out, "maxLength"); } }
        }
        if let V::Arr(a) = inst {
            let n = a.len() as f64;
            if let Some(v) = m.get("minItems") { if !(n >= v.num()) { add(out, "minItems"); } }
            if let Some(v) = m.get("maxItems") { if !(n <= v.num()) { add(out, "maxItems"); } }
            if let Some(V::Bool(true)) = m.get("uniqueItems") {
                let mut dup = false;
                'o: for x in 0..a.len() {
                    for y in x + 1..a.len() {
                        if deep_eq(&a[x], &a[y]) {
                            dup = true;
                            break 'o;
                        }
                    }
                }
                if dup {
                    add(out, "uniqueItems");
                }
            }
            let mut pl = 0;
            if let Some(V::Arr(pi)) = m.get("prefixItems") {
                pl = pi.len();
                for k in 0..pl.min(a.len()) {
                    self.validate(&a[k], &format!("{}/{}", l, k), &pi[k], &format!("{}/prefixItems/{}", p, k), out);
                }
            }
            if let Some(items) = m.get("items") {
                let ip = format!("{}/items", p);
                for k in pl..a.len() {
                    self.validate(&a[k], &format!("{}/{}", l, k), items, &ip, out);
                }
            }
        }
        if let V::Obj(o) = inst {
            let props = m.get("properties");
            for (k, v) in o {
                if let Some(ps) = props.and_then(|pp| pp.get(k)) {
                    self.validate(v, &format!("{}/{}", l, esc(k)), ps, &format!("{}/properties/{}", p, esc(k)), out);
                }
            }
            if let Some(V::Arr(r)) = m.get("required") {
                for (j, name) in r.iter().enumerate() {
                    if let V::Str(name) = name {
                        if inst.get(name).is_none() {
                            out.insert((l.to_string(), format!("{}/required/{}", p, j)));
                        }
                    }
                }
            }
            if let Some(ap) = m.get("additionalProperties") {
                let app = format!("{}/additionalProperties", p);
                for (k, v) in o {
                    let inprops = props.map_or(false, |pp| pp.get(k).is_some());
                    if !inprops {
                        self.validate(v, &format!("{}/{}", l, esc(k)), ap, &app, out);
                    }
                }
            }
            let n = o.len() as f64;
            if let Some(v) = m.get("minProperties") { if !(n >= v.num()) { add(out, "minProperties"); } }
            if let Some(v) = m.get("maxProperties") { if !(n <= v.num()) { add(out, "maxProperties"); } }
        }
        if let Some(V::Arr(subs)) = m.get("allOf") {
            for (k, sub) in subs.iter().enumerate() {
                self.validate(inst, l, sub, &format!("{}/allOf/{}", p, k), out);
            }
        }
        if let Some(V::Arr(subs)) = m.get("anyOf") {
            let ok = subs.iter().enumerate().any(|(k, sub)| self.accepts(inst, l, sub, &format!("{}/anyOf/{}", p, k)));
            if !ok {
                add(out, "anyOf");
            }
        }
        if let Some(V::Arr(subs)) = m.get("oneOf") {
            let c = subs.iter().enumerate().filter(|(k, sub)| self.accepts(inst, l, sub, &format!("{}/oneOf/{}", p, k))).count();
            if c != 1 {
                add(out, "oneOf");
            }
        }
        if let Some(sub) = m.get("not") {
            if self.accepts(inst, l, sub, &format!("{}/not", p)) {
                add(out, "not");
            }
        }
        if let Some(ifs) = m.get("if") {
            if self.accepts(inst, l, ifs, &format!("{}/if", p)) {
                if let Some(t) = m.get("then") {
                    self.validate(inst, l, t, &format!("{}/then", p), out);
                }
            } else if let Some(e) = m.get("else") {
                self.validate(inst, l, e, &format!("{}/else", p), out);
            }
        }
        if let Some(V::Str(r)) = m.get("$ref") {
            if r == "#" {
                self.validate(inst, l, &self.root, "#", out);
            } else {
                let name = r["#/$defs/".len()..].replace("~1", "/").replace("~0", "~");
                if let Some(target) = self.root.get("$defs").and_then(|d| d.get(&name)) {
                    self.validate(inst, l, target, &format!("#/$defs/{}", esc(&name)), out);
                }
            }
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dir = Path::new(&args[1]);
    let sb = fs::read(dir.join("schema.json")).unwrap();
    let ib = fs::read(dir.join("instances.json")).unwrap();
    let root = P { s: &sb, i: 0 }.value();
    let insts = P { s: &ib, i: 0 }.value();
    let ctx = Ctx { root };
    let mut o = String::new();
    let (mut valid, mut invalid) = (0, 0);
    if let V::Arr(list) = &insts {
        for (n, inst) in list.iter().enumerate() {
            let mut errs = Errs::new();
            ctx.validate(inst, "#", &ctx.root, "#", &mut errs);
            if errs.is_empty() {
                valid += 1;
                o.push_str(&format!("{}: valid\n", n));
            } else {
                invalid += 1;
                o.push_str(&format!("{}: invalid ({})\n", n, errs.len()));
                for (a, b) in &errs {
                    o.push_str(&format!("  {} {}\n", a, b));
                }
            }
        }
    }
    o.push_str(&format!("valid={} invalid={}\n", valid, invalid));
    std::io::stdout().write_all(o.as_bytes()).unwrap();
}
