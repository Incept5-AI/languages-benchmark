use std::cell::RefCell;
use std::collections::HashMap;
use std::io::Write;
use std::rc::Rc;

#[derive(Clone)]
enum Value {
    Nil,
    Int(i64),
    Bool(bool),
    Str(Rc<str>),
    List(Rc<RefCell<Vec<Value>>>),
    Func(Rc<Closure>),
    Builtin(u8),
}

struct Closure {
    def: Rc<FnDef>,
    env: Rc<Scope>,
}

struct FnDef {
    params: Vec<u32>,
    body: Vec<Stmt>,
}

struct Scope {
    parent: Option<Rc<Scope>>,
    vars: RefCell<Vec<(u32, Value)>>,
}

impl Scope {
    fn new(parent: Option<Rc<Scope>>, cap: usize) -> Rc<Scope> {
        Rc::new(Scope { parent, vars: RefCell::new(Vec::with_capacity(cap)) })
    }
    fn declare(&self, id: u32, v: Value) {
        self.vars.borrow_mut().push((id, v));
    }
}

struct TErr {
    line: u32,
    msg: String,
}
type R<T> = Result<T, TErr>;
fn err<T>(line: u32, msg: String) -> R<T> {
    Err(TErr { line, msg })
}

enum Expr {
    Lit(Value),
    Var(u32, u32),
    ListLit(Vec<Expr>),
    FnExpr(Rc<FnDef>),
    Call(Box<Expr>, Vec<Expr>, u32),
    Index(Box<Expr>, Box<Expr>, u32),
    And(Box<Expr>, Box<Expr>),
    Or(Box<Expr>, Box<Expr>),
    Not(Box<Expr>),
    Neg(Box<Expr>, u32),
    Bin(u8, Box<Expr>, Box<Expr>, u32),
}

enum Stmt {
    Let(u32, Expr),
    FnDecl(u32, Rc<FnDef>),
    If(Expr, Vec<Stmt>, Option<Box<Stmt>>),
    Block(Vec<Stmt>),
    While(Expr, Vec<Stmt>),
    For(u32, Expr, Vec<Stmt>, u32),
    Return(Option<Expr>),
    Break,
    Continue,
    Assign(u32, u32, Expr),
    IndexAssign(Expr, Expr, Expr, u32),
    ExprStmt(Expr),
}

enum Flow {
    Normal,
    Break,
    Continue,
    Return(Value),
}

const OPS: [&str; 11] = ["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">="];
const BUILTINS: [&str; 8] = ["print", "str", "type", "len", "push", "pop", "range", "slice"];
const ARITY: [i32; 8] = [-1, 1, 1, 1, 2, 1, 2, 3];

// ---------- lexer ----------
#[derive(Clone, PartialEq)]
enum TK {
    Int(i64),
    Str(String),
    Id(String),
    Kw(String),
    Op(String),
    Eof,
}
struct Tok {
    k: TK,
    line: u32,
}

const KWS: [&str; 16] = [
    "let", "fn", "if", "else", "while", "for", "in", "return", "break", "continue", "and", "or", "not", "true", "false", "nil",
];

fn lex(s: &[u8]) -> Vec<Tok> {
    let mut out = Vec::new();
    let mut i = 0;
    let mut line = 1u32;
    let n = s.len();
    while i < n {
        let c = s[i];
        if c == b'\n' {
            line += 1;
            i += 1;
            continue;
        }
        if c == b' ' || c == b'\t' || c == b'\r' {
            i += 1;
            continue;
        }
        if c == b'#' {
            while i < n && s[i] != b'\n' {
                i += 1;
            }
            continue;
        }
        if c.is_ascii_digit() {
            let j0 = i;
            while i < n && s[i].is_ascii_digit() {
                i += 1;
            }
            let t = std::str::from_utf8(&s[j0..i]).unwrap();
            out.push(Tok { k: TK::Int(t.parse::<i64>().unwrap()), line });
            continue;
        }
        if c.is_ascii_alphabetic() || c == b'_' {
            let j0 = i;
            while i < n && (s[i].is_ascii_alphanumeric() || s[i] == b'_') {
                i += 1;
            }
            let w = String::from_utf8_lossy(&s[j0..i]).to_string();
            if KWS.contains(&w.as_str()) {
                out.push(Tok { k: TK::Kw(w), line });
            } else {
                out.push(Tok { k: TK::Id(w), line });
            }
            continue;
        }
        if c == b'"' {
            let mut sb: Vec<u8> = Vec::new();
            i += 1;
            while i < n && s[i] != b'"' {
                if s[i] == b'\\' && i + 1 < n {
                    let e = s[i + 1];
                    sb.push(match e {
                        b'n' => b'\n',
                        b't' => b'\t',
                        x => x,
                    });
                    i += 2;
                } else {
                    sb.push(s[i]);
                    i += 1;
                }
            }
            i += 1;
            out.push(Tok { k: TK::Str(String::from_utf8_lossy(&sb).to_string()), line });
            continue;
        }
        if i + 1 < n {
            let two = &s[i..i + 2];
            if two == b"==" || two == b"!=" || two == b"<=" || two == b">=" {
                out.push(Tok { k: TK::Op(String::from_utf8_lossy(two).to_string()), line });
                i += 2;
                continue;
            }
        }
        out.push(Tok { k: TK::Op((c as char).to_string()), line });
        i += 1;
    }
    out.push(Tok { k: TK::Eof, line });
    out
}

// ---------- parser ----------
struct Parser {
    toks: Vec<Tok>,
    pos: usize,
    names: HashMap<String, u32>,
    name_list: Vec<String>,
}

impl Parser {
    fn intern(&mut self, s: &str) -> u32 {
        if let Some(&i) = self.names.get(s) {
            return i;
        }
        let i = self.name_list.len() as u32;
        self.names.insert(s.to_string(), i);
        self.name_list.push(s.to_string());
        i
    }
    fn peek(&self) -> &Tok {
        &self.toks[self.pos]
    }
    fn next(&mut self) -> &Tok {
        self.pos += 1;
        &self.toks[self.pos - 1]
    }
    fn is_op(&self, t: &str) -> bool {
        matches!(&self.peek().k, TK::Op(o) if o == t)
    }
    fn is_kw(&self, t: &str) -> bool {
        matches!(&self.peek().k, TK::Kw(o) if o == t)
    }
    fn expect(&mut self, _t: &str) -> u32 {
        let l = self.peek().line;
        self.pos += 1;
        l
    }
    fn ident(&mut self) -> (u32, u32) {
        let t = self.next();
        let line = t.line;
        let name = match &t.k {
            TK::Id(s) => s.clone(),
            _ => panic!("expected identifier at line {}", line),
        };
        (self.intern(&name), line)
    }
    fn block(&mut self) -> Vec<Stmt> {
        self.expect("{");
        let mut v = Vec::new();
        while !self.is_op("}") {
            v.push(self.stmt());
        }
        self.expect("}");
        v
    }
    fn fn_rest(&mut self) -> Rc<FnDef> {
        self.expect("(");
        let mut ps = Vec::new();
        if !self.is_op(")") {
            ps.push(self.ident().0);
            while self.is_op(",") {
                self.next();
                ps.push(self.ident().0);
            }
        }
        self.expect(")");
        let body = self.block();
        Rc::new(FnDef { params: ps, body })
    }
    fn if_stmt(&mut self) -> Stmt {
        self.expect("if");
        let c = self.expr();
        let t = self.block();
        let mut e = None;
        if self.is_kw("else") {
            self.next();
            if self.is_kw("if") {
                e = Some(Box::new(self.if_stmt()));
            } else {
                e = Some(Box::new(Stmt::Block(self.block())));
            }
        }
        Stmt::If(c, t, e)
    }
    fn stmt(&mut self) -> Stmt {
        let kw = match &self.peek().k {
            TK::Kw(k) => Some(k.clone()),
            _ => None,
        };
        if let Some(k) = kw {
            match k.as_str() {
                "let" => {
                    self.next();
                    let (id, _) = self.ident();
                    self.expect("=");
                    let e = self.expr();
                    self.expect(";");
                    return Stmt::Let(id, e);
                }
                "fn" => {
                    if matches!(self.toks[self.pos + 1].k, TK::Id(_)) {
                        self.next();
                        let (id, _) = self.ident();
                        let d = self.fn_rest();
                        return Stmt::FnDecl(id, d);
                    }
                }
                "if" => return self.if_stmt(),
                "while" => {
                    self.next();
                    let c = self.expr();
                    let b = self.block();
                    return Stmt::While(c, b);
                }
                "for" => {
                    let line = self.next().line;
                    let (id, _) = self.ident();
                    self.expect("in");
                    let e = self.expr();
                    let b = self.block();
                    return Stmt::For(id, e, b, line);
                }
                "return" => {
                    self.next();
                    let e = if self.is_op(";") { None } else { Some(self.expr()) };
                    self.expect(";");
                    return Stmt::Return(e);
                }
                "break" => {
                    self.next();
                    self.expect(";");
                    return Stmt::Break;
                }
                "continue" => {
                    self.next();
                    self.expect(";");
                    return Stmt::Continue;
                }
                _ => {}
            }
        }
        let e = self.expr();
        if self.is_op("=") {
            self.next();
            let v = self.expr();
            self.expect(";");
            return match e {
                Expr::Var(id, line) => Stmt::Assign(id, line, v),
                Expr::Index(o, i, line) => Stmt::IndexAssign(*o, *i, v, line),
                _ => panic!("bad assignment target"),
            };
        }
        self.expect(";");
        Stmt::ExprStmt(e)
    }
    fn expr(&mut self) -> Expr {
        let mut l = self.and_e();
        while self.is_kw("or") {
            self.next();
            let r = self.and_e();
            l = Expr::Or(Box::new(l), Box::new(r));
        }
        l
    }
    fn and_e(&mut self) -> Expr {
        let mut l = self.not_e();
        while self.is_kw("and") {
            self.next();
            let r = self.not_e();
            l = Expr::And(Box::new(l), Box::new(r));
        }
        l
    }
    fn not_e(&mut self) -> Expr {
        if self.is_kw("not") {
            self.next();
            return Expr::Not(Box::new(self.not_e()));
        }
        self.cmp()
    }
    fn op_index(&self) -> i32 {
        if let TK::Op(o) = &self.peek().k {
            for (i, x) in OPS.iter().enumerate() {
                if x == o {
                    return i as i32;
                }
            }
        }
        -1
    }
    fn cmp(&mut self) -> Expr {
        let mut l = self.add();
        loop {
            let o = self.op_index();
            if o < 5 {
                break;
            }
            let line = self.next().line;
            let r = self.add();
            l = Expr::Bin(o as u8, Box::new(l), Box::new(r), line);
        }
        l
    }
    fn add(&mut self) -> Expr {
        let mut l = self.mul();
        loop {
            let o = self.op_index();
            if o != 0 && o != 1 {
                break;
            }
            let line = self.next().line;
            let r = self.mul();
            l = Expr::Bin(o as u8, Box::new(l), Box::new(r), line);
        }
        l
    }
    fn mul(&mut self) -> Expr {
        let mut l = self.unary();
        loop {
            let o = self.op_index();
            if o != 2 && o != 3 && o != 4 {
                break;
            }
            let line = self.next().line;
            let r = self.unary();
            l = Expr::Bin(o as u8, Box::new(l), Box::new(r), line);
        }
        l
    }
    fn unary(&mut self) -> Expr {
        if self.is_op("-") {
            let line = self.next().line;
            return Expr::Neg(Box::new(self.unary()), line);
        }
        self.postfix()
    }
    fn args(&mut self, close: &str) -> Vec<Expr> {
        let mut a = Vec::new();
        if !self.is_op(close) {
            a.push(self.expr());
            while self.is_op(",") {
                self.next();
                a.push(self.expr());
            }
        }
        self.expect(close);
        a
    }
    fn postfix(&mut self) -> Expr {
        let mut e = self.primary();
        loop {
            if self.is_op("(") {
                let line = self.next().line;
                let a = self.args(")");
                e = Expr::Call(Box::new(e), a, line);
            } else if self.is_op("[") {
                let line = self.next().line;
                let i = self.expr();
                self.expect("]");
                e = Expr::Index(Box::new(e), Box::new(i), line);
            } else {
                break;
            }
        }
        e
    }
    fn primary(&mut self) -> Expr {
        let t = self.next();
        let line = t.line;
        let k = t.k.clone();
        match k {
            TK::Int(v) => Expr::Lit(Value::Int(v)),
            TK::Str(s) => Expr::Lit(Value::Str(Rc::from(s.as_str()))),
            TK::Id(s) => Expr::Var(self.intern(&s), line),
            TK::Kw(k) => match k.as_str() {
                "true" => Expr::Lit(Value::Bool(true)),
                "false" => Expr::Lit(Value::Bool(false)),
                "nil" => Expr::Lit(Value::Nil),
                "fn" => Expr::FnExpr(self.fn_rest()),
                _ => panic!("parse error line {}", line),
            },
            TK::Op(o) => {
                if o == "(" {
                    let e = self.expr();
                    self.expect(")");
                    e
                } else if o == "[" {
                    Expr::ListLit(self.args("]"))
                } else {
                    panic!("parse error line {}", line)
                }
            }
            TK::Eof => panic!("unexpected eof"),
        }
    }
}

// ---------- runtime ----------
struct Interp {
    names: Vec<String>,
    out: Vec<u8>,
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Nil => false,
        Value::Bool(b) => *b,
        Value::Int(i) => *i != 0,
        Value::Str(s) => !s.is_empty(),
        Value::List(l) => !l.borrow().is_empty(),
        _ => true,
    }
}

fn type_name(v: &Value) -> &'static str {
    match v {
        Value::Nil => "nil",
        Value::Int(_) => "int",
        Value::Bool(_) => "bool",
        Value::Str(_) => "string",
        Value::List(_) => "list",
        _ => "function",
    }
}

fn val_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Nil, Value::Nil) => true,
        (Value::Int(x), Value::Int(y)) => x == y,
        (Value::Bool(x), Value::Bool(y)) => x == y,
        (Value::Str(x), Value::Str(y)) => x == y,
        (Value::List(x), Value::List(y)) => {
            if Rc::ptr_eq(x, y) {
                return true;
            }
            let x = x.borrow();
            let y = y.borrow();
            x.len() == y.len() && x.iter().zip(y.iter()).all(|(p, q)| val_eq(p, q))
        }
        (Value::Func(x), Value::Func(y)) => Rc::ptr_eq(x, y),
        (Value::Builtin(x), Value::Builtin(y)) => x == y,
        _ => false,
    }
}

fn display(v: &Value, out: &mut Vec<u8>, in_list: bool, stack: &mut Vec<*const RefCell<Vec<Value>>>) {
    match v {
        Value::Nil => out.extend_from_slice(b"nil"),
        Value::Int(i) => out.extend_from_slice(i.to_string().as_bytes()),
        Value::Bool(b) => out.extend_from_slice(if *b { b"true" } else { b"false" }),
        Value::Str(s) => {
            if !in_list {
                out.extend_from_slice(s.as_bytes());
            } else {
                out.push(b'"');
                for &c in s.as_bytes() {
                    match c {
                        b'\\' => out.extend_from_slice(b"\\\\"),
                        b'"' => out.extend_from_slice(b"\\\""),
                        b'\n' => out.extend_from_slice(b"\\n"),
                        b'\t' => out.extend_from_slice(b"\\t"),
                        _ => out.push(c),
                    }
                }
                out.push(b'"');
            }
        }
        Value::List(l) => {
            let p = Rc::as_ptr(l);
            if stack.contains(&p) {
                out.extend_from_slice(b"[...]");
                return;
            }
            stack.push(p);
            out.push(b'[');
            let items = l.borrow();
            for (i, x) in items.iter().enumerate() {
                if i > 0 {
                    out.extend_from_slice(b", ");
                }
                display(x, out, true, stack);
            }
            out.push(b']');
            stack.pop();
        }
        _ => out.extend_from_slice(b"<fn>"),
    }
}

fn disp_string(v: &Value) -> String {
    let mut o = Vec::new();
    display(v, &mut o, false, &mut Vec::new());
    String::from_utf8_lossy(&o).to_string()
}

fn lookup(s: &Rc<Scope>, id: u32) -> Option<Value> {
    let mut c: &Scope = s;
    loop {
        {
            let vars = c.vars.borrow();
            for (k, v) in vars.iter().rev() {
                if *k == id {
                    return Some(v.clone());
                }
            }
        }
        match &c.parent {
            Some(p) => c = p,
            None => return None,
        }
    }
}

fn assign(s: &Rc<Scope>, id: u32, val: Value) -> bool {
    let mut c: &Scope = s;
    loop {
        {
            let mut vars = c.vars.borrow_mut();
            for (k, v) in vars.iter_mut().rev() {
                if *k == id {
                    *v = val;
                    return true;
                }
            }
        }
        match &c.parent {
            Some(p) => c = p,
            None => return false,
        }
    }
}

impl Interp {
    fn eval(&mut self, e: &Expr, s: &Rc<Scope>) -> R<Value> {
        match e {
            Expr::Lit(v) => Ok(v.clone()),
            Expr::Var(id, line) => match lookup(s, *id) {
                Some(v) => Ok(v),
                None => err(*line, format!("undefined variable {}", self.names[*id as usize])),
            },
            Expr::ListLit(items) => {
                let mut v = Vec::with_capacity(items.len());
                for it in items {
                    v.push(self.eval(it, s)?);
                }
                Ok(Value::List(Rc::new(RefCell::new(v))))
            }
            Expr::FnExpr(d) => Ok(Value::Func(Rc::new(Closure { def: d.clone(), env: s.clone() }))),
            Expr::Call(c, args, line) => {
                let f = self.eval(c, s)?;
                let mut av = Vec::with_capacity(args.len());
                for a in args {
                    av.push(self.eval(a, s)?);
                }
                self.call(f, av, *line)
            }
            Expr::Index(o, i, line) => {
                let ov = self.eval(o, s)?;
                let iv = self.eval(i, s)?;
                if let Value::Int(k) = iv {
                    match &ov {
                        Value::List(l) => {
                            let l = l.borrow();
                            if k < 0 || k >= l.len() as i64 {
                                return err(*line, format!("index {} out of range for length {}", k, l.len()));
                            }
                            return Ok(l[k as usize].clone());
                        }
                        Value::Str(st) => {
                            let b = st.as_bytes();
                            if k < 0 || k >= b.len() as i64 {
                                return err(*line, format!("index {} out of range for length {}", k, b.len()));
                            }
                            let ch = (b[k as usize] as char).to_string();
                            return Ok(Value::Str(Rc::from(ch.as_str())));
                        }
                        _ => {}
                    }
                }
                err(*line, format!("cannot index {} with {}", type_name(&ov), type_name(&iv)))
            }
            Expr::And(l, r) => {
                let a = self.eval(l, s)?;
                if !truthy(&a) {
                    return Ok(a);
                }
                self.eval(r, s)
            }
            Expr::Or(l, r) => {
                let a = self.eval(l, s)?;
                if truthy(&a) {
                    return Ok(a);
                }
                self.eval(r, s)
            }
            Expr::Not(x) => {
                let a = self.eval(x, s)?;
                Ok(Value::Bool(!truthy(&a)))
            }
            Expr::Neg(x, line) => {
                let a = self.eval(x, s)?;
                match a {
                    Value::Int(i) => match i.checked_neg() {
                        Some(v) => Ok(Value::Int(v)),
                        None => err(*line, "integer overflow".to_string()),
                    },
                    _ => err(*line, format!("cannot apply - to {}", type_name(&a))),
                }
            }
            Expr::Bin(op, l, r, line) => {
                let a = self.eval(l, s)?;
                let b = self.eval(r, s)?;
                let line = *line;
                let op = *op;
                let bad = |a: &Value, b: &Value| -> R<Value> {
                    err(line, format!("cannot apply {} to {} and {}", OPS[op as usize], type_name(a), type_name(b)))
                };
                let ovf = || -> R<Value> { err(line, "integer overflow".to_string()) };
                match op {
                    5 => Ok(Value::Bool(val_eq(&a, &b))),
                    6 => Ok(Value::Bool(!val_eq(&a, &b))),
                    0 => match (&a, &b) {
                        (Value::Int(x), Value::Int(y)) => match x.checked_add(*y) {
                            Some(z) => Ok(Value::Int(z)),
                            None => ovf(),
                        },
                        (Value::Str(x), Value::Str(y)) => {
                            let mut n = String::with_capacity(x.len() + y.len());
                            n.push_str(x);
                            n.push_str(y);
                            Ok(Value::Str(Rc::from(n.as_str())))
                        }
                        (Value::List(x), Value::List(y)) => {
                            let mut n = x.borrow().clone();
                            n.extend(y.borrow().iter().cloned());
                            Ok(Value::List(Rc::new(RefCell::new(n))))
                        }
                        _ => bad(&a, &b),
                    },
                    7 | 8 | 9 | 10 => {
                        let c = match (&a, &b) {
                            (Value::Int(x), Value::Int(y)) => x.cmp(y),
                            (Value::Str(x), Value::Str(y)) => x.as_bytes().cmp(y.as_bytes()),
                            _ => return bad(&a, &b),
                        };
                        use std::cmp::Ordering::*;
                        Ok(Value::Bool(match op {
                            7 => c == Less,
                            8 => c != Greater,
                            9 => c == Greater,
                            _ => c != Less,
                        }))
                    }
                    _ => {
                        let (x, y) = match (&a, &b) {
                            (Value::Int(x), Value::Int(y)) => (*x, *y),
                            _ => return bad(&a, &b),
                        };
                        match op {
                            1 => x.checked_sub(y).map(Value::Int).map_or_else(ovf, Ok),
                            2 => x.checked_mul(y).map(Value::Int).map_or_else(ovf, Ok),
                            3 => {
                                if y == 0 {
                                    return err(line, "division by zero".to_string());
                                }
                                x.checked_div(y).map(Value::Int).map_or_else(ovf, Ok)
                            }
                            _ => {
                                if y == 0 {
                                    return err(line, "division by zero".to_string());
                                }
                                if y == -1 {
                                    return Ok(Value::Int(0));
                                }
                                Ok(Value::Int(x % y))
                            }
                        }
                    }
                }
            }
        }
    }

    fn run_in(&mut self, body: &[Stmt], s: &Rc<Scope>) -> R<Flow> {
        for st in body {
            let f = self.exec(st, s)?;
            if !matches!(f, Flow::Normal) {
                return Ok(f);
            }
        }
        Ok(Flow::Normal)
    }

    fn exec(&mut self, st: &Stmt, s: &Rc<Scope>) -> R<Flow> {
        match st {
            Stmt::Let(id, e) => {
                let v = self.eval(e, s)?;
                s.declare(*id, v);
                Ok(Flow::Normal)
            }
            Stmt::FnDecl(id, d) => {
                s.declare(*id, Value::Func(Rc::new(Closure { def: d.clone(), env: s.clone() })));
                Ok(Flow::Normal)
            }
            Stmt::If(c, t, e) => {
                let cv = self.eval(c, s)?;
                if truthy(&cv) {
                    let sc = Scope::new(Some(s.clone()), 4);
                    self.run_in(t, &sc)
                } else if let Some(e) = e {
                    self.exec(e, s)
                } else {
                    Ok(Flow::Normal)
                }
            }
            Stmt::Block(b) => {
                let sc = Scope::new(Some(s.clone()), 4);
                self.run_in(b, &sc)
            }
            Stmt::While(c, b) => {
                loop {
                    let cv = self.eval(c, s)?;
                    if !truthy(&cv) {
                        break;
                    }
                    let sc = Scope::new(Some(s.clone()), 4);
                    match self.run_in(b, &sc)? {
                        Flow::Break => break,
                        Flow::Return(v) => return Ok(Flow::Return(v)),
                        _ => {}
                    }
                }
                Ok(Flow::Normal)
            }
            Stmt::For(id, e, b, line) => {
                let v = self.eval(e, s)?;
                let l = match &v {
                    Value::List(l) => l.clone(),
                    _ => return err(*line, format!("cannot iterate {}", type_name(&v))),
                };
                let mut i = 0usize;
                loop {
                    let item = {
                        let lb = l.borrow();
                        if i >= lb.len() {
                            break;
                        }
                        lb[i].clone()
                    };
                    let sc = Scope::new(Some(s.clone()), 4);
                    sc.declare(*id, item);
                    match self.run_in(b, &sc)? {
                        Flow::Break => break,
                        Flow::Return(v) => return Ok(Flow::Return(v)),
                        _ => {}
                    }
                    i += 1;
                }
                Ok(Flow::Normal)
            }
            Stmt::Return(e) => {
                let v = match e {
                    Some(e) => self.eval(e, s)?,
                    None => Value::Nil,
                };
                Ok(Flow::Return(v))
            }
            Stmt::Break => Ok(Flow::Break),
            Stmt::Continue => Ok(Flow::Continue),
            Stmt::Assign(id, line, e) => {
                let v = self.eval(e, s)?;
                if assign(s, *id, v) {
                    Ok(Flow::Normal)
                } else {
                    err(*line, format!("undefined variable {}", self.names[*id as usize]))
                }
            }
            Stmt::IndexAssign(o, i, e, line) => {
                let ov = self.eval(o, s)?;
                let iv = self.eval(i, s)?;
                let v = self.eval(e, s)?;
                let l = match &ov {
                    Value::List(l) => l,
                    _ => return err(*line, format!("cannot assign into {}", type_name(&ov))),
                };
                let k = match iv {
                    Value::Int(k) => k,
                    _ => return err(*line, format!("cannot index list with {}", type_name(&iv))),
                };
                let mut lb = l.borrow_mut();
                if k < 0 || k >= lb.len() as i64 {
                    return err(*line, format!("index {} out of range for length {}", k, lb.len()));
                }
                lb[k as usize] = v;
                Ok(Flow::Normal)
            }
            Stmt::ExprStmt(e) => {
                self.eval(e, s)?;
                Ok(Flow::Normal)
            }
        }
    }

    fn call(&mut self, f: Value, av: Vec<Value>, line: u32) -> R<Value> {
        match &f {
            Value::Func(c) => {
                let ps = &c.def.params;
                if ps.len() != av.len() {
                    return err(line, format!("expected {} arguments, got {}", ps.len(), av.len()));
                }
                let sc = Scope::new(Some(c.env.clone()), ps.len() + 2);
                for (p, v) in ps.iter().zip(av.into_iter()) {
                    sc.declare(*p, v);
                }
                let def = c.def.clone();
                for st in def.body.iter() {
                    if let Flow::Return(v) = self.exec(st, &sc)? {
                        return Ok(v);
                    }
                }
                Ok(Value::Nil)
            }
            Value::Builtin(id) => {
                let id = *id as usize;
                let ar = ARITY[id];
                if ar >= 0 && av.len() as i32 != ar {
                    return err(line, format!("expected {} arguments, got {}", ar, av.len()));
                }
                match id {
                    0 => {
                        let mut stack = Vec::new();
                        for (i, v) in av.iter().enumerate() {
                            if i > 0 {
                                self.out.push(b' ');
                            }
                            display(v, &mut self.out, false, &mut stack);
                        }
                        self.out.push(b'\n');
                        if self.out.len() > 1 << 16 {
                            let so = std::io::stdout();
                            let mut so = so.lock();
                            so.write_all(&self.out).unwrap();
                            self.out.clear();
                        }
                        return Ok(Value::Nil);
                    }
                    1 => return Ok(Value::Str(Rc::from(disp_string(&av[0]).as_str()))),
                    2 => return Ok(Value::Str(Rc::from(type_name(&av[0])))),
                    3 => match &av[0] {
                        Value::Str(s) => return Ok(Value::Int(s.len() as i64)),
                        Value::List(l) => return Ok(Value::Int(l.borrow().len() as i64)),
                        _ => {}
                    },
                    4 => {
                        if let Value::List(l) = &av[0] {
                            l.borrow_mut().push(av[1].clone());
                            return Ok(Value::Nil);
                        }
                    }
                    5 => {
                        if let Value::List(l) = &av[0] {
                            if let Some(v) = l.borrow_mut().pop() {
                                return Ok(v);
                            }
                        }
                    }
                    6 => {
                        if let (Value::Int(a), Value::Int(b)) = (&av[0], &av[1]) {
                            let mut v = Vec::new();
                            let mut k = *a;
                            while k < *b {
                                v.push(Value::Int(k));
                                k += 1;
                            }
                            return Ok(Value::List(Rc::new(RefCell::new(v))));
                        }
                    }
                    _ => {
                        if let (Value::Int(a), Value::Int(b)) = (&av[1], &av[2]) {
                            let (a, b) = (*a, *b);
                            match &av[0] {
                                Value::Str(s) => {
                                    if 0 <= a && a <= b && b <= s.len() as i64 {
                                        return Ok(Value::Str(Rc::from(&s[a as usize..b as usize])));
                                    }
                                }
                                Value::List(l) => {
                                    let l = l.borrow();
                                    if 0 <= a && a <= b && b <= l.len() as i64 {
                                        let v = l[a as usize..b as usize].to_vec();
                                        return Ok(Value::List(Rc::new(RefCell::new(v))));
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                }
                err(line, format!("bad argument to {}", BUILTINS[id]))
            }
            _ => err(line, format!("cannot call {}", type_name(&f))),
        }
    }
}

fn run() {
    let args: Vec<String> = std::env::args().collect();
    let path = std::path::Path::new(&args[1]).join("program.tern");
    let src = std::fs::read(path).unwrap();
    let toks = lex(&src);
    let mut p = Parser { toks, pos: 0, names: HashMap::new(), name_list: Vec::new() };
    let builtins = Scope::new(None, 8);
    for (i, b) in BUILTINS.iter().enumerate() {
        let id = p.intern(b);
        builtins.declare(id, Value::Builtin(i as u8));
    }
    let mut prog = Vec::new();
    while !matches!(p.peek().k, TK::Eof) {
        prog.push(p.stmt());
    }
    let global = Scope::new(Some(builtins), 32);
    let mut it = Interp { names: p.name_list.clone(), out: Vec::new() };
    for st in prog.iter() {
        if let Err(e) = it.exec(st, &global) {
            it.out.extend_from_slice(format!("error at line {}: {}\n", e.line, e.msg).as_bytes());
            break;
        }
    }
    let so = std::io::stdout();
    let mut so = so.lock();
    so.write_all(&it.out).unwrap();
    so.flush().unwrap();
    // avoid slow/deep drop of cyclic structures
    std::mem::forget(it);
    std::mem::forget(prog);
    std::mem::forget(global);
}

fn main() {
    let t = std::thread::Builder::new().stack_size(1 << 29).spawn(run).unwrap();
    t.join().unwrap();
}
