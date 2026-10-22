use std::fs;

#[derive(Clone)]
enum Json {
    Null,
    Bool(bool),
    // Keep the original textual form so integers stay integers.
    Num(String),
    // Raw escaped content WITHOUT surrounding quotes; re-emitted verbatim.
    Str(String),
    Arr(Vec<Json>),
    // Insertion order preserved; sorted at output time.
    Obj(Vec<(String, Json)>),
}

struct Parser {
    chars: Vec<char>,
    i: usize,
}

impl Parser {
    fn new(s: &str) -> Parser {
        Parser { chars: s.chars().collect(), i: 0 }
    }

    fn skip_ws(&mut self) {
        while self.i < self.chars.len() {
            match self.chars[self.i] {
                ' ' | '\t' | '\n' | '\r' => self.i += 1,
                _ => break,
            }
        }
    }

    fn peek(&self) -> char {
        self.chars[self.i]
    }

    fn parse_value(&mut self) -> Json {
        self.skip_ws();
        let c = self.peek();
        match c {
            '{' => self.parse_object(),
            '[' => self.parse_array(),
            '"' => Json::Str(self.parse_string_raw()),
            't' => { self.expect("true"); Json::Bool(true) }
            'f' => { self.expect("false"); Json::Bool(false) }
            'n' => { self.expect("null"); Json::Null }
            _ => self.parse_number(),
        }
    }

    fn expect(&mut self, lit: &str) {
        for ch in lit.chars() {
            if self.i >= self.chars.len() || self.chars[self.i] != ch {
                panic!("expected literal {}", lit);
            }
            self.i += 1;
        }
    }

    fn parse_string_raw(&mut self) -> String {
        // assumes current char is '"'
        self.i += 1; // opening quote
        let mut out = String::new();
        while self.i < self.chars.len() {
            let c = self.chars[self.i];
            if c == '"' {
                self.i += 1;
                return out;
            }
            if c == '\\' {
                // copy escape verbatim
                out.push(c);
                self.i += 1;
                if self.i >= self.chars.len() {
                    panic!("bad escape");
                }
                let e = self.chars[self.i];
                out.push(e);
                self.i += 1;
                if e == 'u' {
                    for _ in 0..4 {
                        if self.i >= self.chars.len() {
                            panic!("bad unicode escape");
                        }
                        out.push(self.chars[self.i]);
                        self.i += 1;
                    }
                }
            } else {
                out.push(c);
                self.i += 1;
            }
        }
        panic!("unterminated string");
    }

    fn parse_number(&mut self) -> Json {
        let start = self.i;
        if self.peek() == '-' {
            self.i += 1;
        }
        while self.i < self.chars.len() {
            match self.chars[self.i] {
                '0'..='9' | '.' | 'e' | 'E' | '+' | '-' => self.i += 1,
                _ => break,
            }
        }
        let s: String = self.chars[start..self.i].iter().collect();
        Json::Num(s)
    }

    fn parse_array(&mut self) -> Json {
        self.i += 1; // '['
        let mut items = Vec::new();
        self.skip_ws();
        if self.i < self.chars.len() && self.peek() == ']' {
            self.i += 1;
            return Json::Arr(items);
        }
        loop {
            self.skip_ws();
            items.push(self.parse_value());
            self.skip_ws();
            match self.peek() {
                ',' => { self.i += 1; }
                ']' => { self.i += 1; break; }
                _ => panic!("expected , or ] in array"),
            }
        }
        Json::Arr(items)
    }

    fn parse_object(&mut self) -> Json {
        self.i += 1; // '{'
        let mut members: Vec<(String, Json)> = Vec::new();
        self.skip_ws();
        if self.i < self.chars.len() && self.peek() == '}' {
            self.i += 1;
            return Json::Obj(members);
        }
        loop {
            self.skip_ws();
            let key = self.parse_string_raw();
            self.skip_ws();
            if self.peek() != ':' {
                panic!("expected : in object");
            }
            self.i += 1;
            self.skip_ws();
            let val = self.parse_value();
            members.push((key, val));
            self.skip_ws();
            match self.peek() {
                ',' => { self.i += 1; }
                '}' => { self.i += 1; break; }
                _ => panic!("expected , or }} in object"),
            }
        }
        Json::Obj(members)
    }
}

fn scalar_eq(a: &Json, b: &Json) -> bool {
    match (a, b) {
        (Json::Null, Json::Null) => true,
        (Json::Bool(x), Json::Bool(y)) => x == y,
        (Json::Num(x), Json::Num(y)) => x == y,
        (Json::Str(x), Json::Str(y)) => x == y,
        _ => false,
    }
}

fn merge_arrays(a: &[Json], b: &[Json]) -> Json {
    let mut out: Vec<Json> = Vec::new();
    for src in [a, b] {
        for cand in src {
            let dup = out.iter().any(|e| scalar_eq(e, cand));
            if !dup {
                out.push(cand.clone());
            }
        }
    }
    Json::Arr(out)
}

fn obj_find<'a>(o: &'a [(String, Json)], key: &str) -> Option<&'a Json> {
    o.iter().find(|(k, _)| k == key).map(|(_, v)| v)
}

fn deep_merge(a: &Json, b: &Json) -> Json {
    match (a, b) {
        (Json::Obj(am), Json::Obj(bm)) => {
            let mut out: Vec<(String, Json)> = Vec::new();
            for (k, av) in am {
                if let Some(bv) = obj_find(bm, k) {
                    out.push((k.clone(), deep_merge(av, bv)));
                } else {
                    out.push((k.clone(), av.clone()));
                }
            }
            for (k, bv) in bm {
                if obj_find(am, k).is_none() {
                    out.push((k.clone(), bv.clone()));
                }
            }
            Json::Obj(out)
        }
        (Json::Arr(av), Json::Arr(bv)) => merge_arrays(av, bv),
        _ => b.clone(),
    }
}

fn indent(out: &mut String, depth: usize) {
    for _ in 0..(depth * 2) {
        out.push(' ');
    }
}

fn emit(out: &mut String, v: &Json, depth: usize) {
    match v {
        Json::Null => out.push_str("null"),
        Json::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Json::Num(n) => out.push_str(n),
        Json::Str(s) => {
            out.push('"');
            out.push_str(s);
            out.push('"');
        }
        Json::Arr(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push_str("[\n");
            for (i, item) in items.iter().enumerate() {
                indent(out, depth + 1);
                emit(out, item, depth + 1);
                if i + 1 < items.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            indent(out, depth);
            out.push(']');
        }
        Json::Obj(members) => {
            if members.is_empty() {
                out.push_str("{}");
                return;
            }
            let mut sorted: Vec<&(String, Json)> = members.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(&b.0));
            out.push_str("{\n");
            for (i, (k, val)) in sorted.iter().enumerate() {
                indent(out, depth + 1);
                out.push('"');
                out.push_str(k);
                out.push_str("\": ");
                emit(out, val, depth + 1);
                if i + 1 < sorted.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            indent(out, depth);
            out.push('}');
        }
    }
}

fn main() {
    let path_a = "__FIXTURE_DIR__/config_a.json";
    let path_b = "__FIXTURE_DIR__/config_b.json";

    let ta = fs::read_to_string(path_a).expect("cannot read config_a.json");
    let tb = fs::read_to_string(path_b).expect("cannot read config_b.json");

    let va = Parser::new(&ta).parse_value();
    let vb = Parser::new(&tb).parse_value();

    let merged = deep_merge(&va, &vb);

    let mut out = String::new();
    emit(&mut out, &merged, 0);
    println!("{}", out);
}
