use std::io::Write;

enum Node {
    Char(u8),
    Any,
    Class(usize),
    Bol,
    Eol,
    Cat(Vec<Node>),
    Alt(Vec<Node>),
    Group(usize, Box<Node>),
    Rep(Box<Node>, usize, i64, bool),
}

fn inset(e: u8, c: u8) -> bool {
    match e {
        b'd' => c.is_ascii_digit(),
        b'w' => c.is_ascii_alphanumeric() || c == b'_',
        b's' => c == b' ' || c == b'\t' || c == b'\n' || c == b'\r' || c == 12 || c == 11,
        b'D' => !inset(b'd', c),
        b'W' => !inset(b'w', c),
        b'S' => !inset(b's', c),
        _ => false,
    }
}
fn is_cls_esc(e: u8) -> bool {
    b"dwsDWS".contains(&e)
}

struct Parser<'a> {
    p: &'a [u8],
    i: usize,
    ngroups: usize,
    classes: Vec<[bool; 128]>,
}

impl<'a> Parser<'a> {
    fn peek(&self, k: usize) -> u8 {
        if self.i + k < self.p.len() { self.p[self.i + k] } else { 0 }
    }
    fn regex(&mut self) -> Node {
        let mut v = vec![self.branch()];
        while self.peek(0) == b'|' {
            self.i += 1;
            v.push(self.branch());
        }
        Node::Alt(v)
    }
    fn readint(&mut self) -> i64 {
        let mut v = 0i64;
        while self.peek(0).is_ascii_digit() {
            v = v * 10 + (self.peek(0) - b'0') as i64;
            self.i += 1;
        }
        v
    }
    fn branch(&mut self) -> Node {
        let mut v = Vec::new();
        while self.i < self.p.len() && self.peek(0) != b'|' && self.peek(0) != b')' {
            let mut a = self.atom();
            let q = self.peek(0);
            let mut mn: i64 = -1;
            let mut mx: i64 = -1;
            if q == b'*' { mn = 0; self.i += 1; }
            else if q == b'+' { mn = 1; self.i += 1; }
            else if q == b'?' { mn = 0; mx = 1; self.i += 1; }
            else if q == b'{' {
                self.i += 1;
                mn = self.readint();
                if self.peek(0) == b'}' { mx = mn; self.i += 1; }
                else {
                    self.i += 1;
                    if self.peek(0) == b'}' { mx = -1; self.i += 1; } else { mx = self.readint(); self.i += 1; }
                }
            }
            if mn >= 0 {
                let mut greedy = true;
                if self.peek(0) == b'?' { greedy = false; self.i += 1; }
                a = Node::Rep(Box::new(a), mn as usize, mx, greedy);
            }
            v.push(a);
        }
        Node::Cat(v)
    }
    fn atom(&mut self) -> Node {
        let ch = self.peek(0);
        match ch {
            b'(' => {
                self.i += 1;
                if self.peek(0) == b'?' && self.peek(1) == b':' {
                    self.i += 2;
                    let r = self.regex();
                    self.i += 1;
                    return r;
                }
                self.ngroups += 1;
                let g = self.ngroups;
                let r = self.regex();
                self.i += 1;
                Node::Group(g, Box::new(r))
            }
            b'.' => { self.i += 1; Node::Any }
            b'^' => { self.i += 1; Node::Bol }
            b'$' => { self.i += 1; Node::Eol }
            b'[' => {
                self.i += 1;
                let mut neg = false;
                if self.peek(0) == b'^' { neg = true; self.i += 1; }
                let mut t = [false; 128];
                while self.i < self.p.len() && self.peek(0) != b']' {
                    let lo: u8;
                    if self.peek(0) == b'\\' {
                        let e = self.peek(1);
                        self.i += 2;
                        if is_cls_esc(e) {
                            for c in 0..128u8 { if inset(e, c) { t[c as usize] = true; } }
                            continue;
                        }
                        lo = e;
                    } else { lo = self.peek(0); self.i += 1; }
                    let mut hi = lo;
                    if self.peek(0) == b'-' && self.peek(1) != b']' {
                        self.i += 1;
                        if self.peek(0) == b'\\' { hi = self.peek(1); self.i += 2; } else { hi = self.peek(0); self.i += 1; }
                    }
                    let mut c = lo as usize;
                    while c <= hi as usize && c < 128 { t[c] = true; c += 1; }
                }
                self.i += 1;
                if neg { for c in 0..128 { t[c] = !t[c]; } }
                self.classes.push(t);
                Node::Class(self.classes.len() - 1)
            }
            b'\\' => {
                let e = self.peek(1);
                self.i += 2;
                if is_cls_esc(e) {
                    let mut t = [false; 128];
                    for c in 0..128u8 { t[c as usize] = inset(e, c); }
                    self.classes.push(t);
                    return Node::Class(self.classes.len() - 1);
                }
                Node::Char(e)
            }
            _ => { self.i += 1; Node::Char(ch) }
        }
    }
}

#[derive(Clone, Copy)]
enum Op { Char, Any, Class, Bol, Eol, Save, Split, Jmp, Loop, IterEnd, Match }
#[derive(Clone, Copy)]
struct Inst { op: Op, a: usize, b: usize, c: bool }

struct Comp { prog: Vec<Inst>, maxlevel: usize }
impl Comp {
    fn emit(&mut self, op: Op, a: usize, b: usize, c: bool) -> usize {
        self.prog.push(Inst { op, a, b, c });
        self.prog.len() - 1
    }
    fn compile(&mut self, n: &Node, level: usize) {
        if level > self.maxlevel { self.maxlevel = level; }
        match n {
            Node::Char(c) => { self.emit(Op::Char, *c as usize, 0, false); }
            Node::Any => { self.emit(Op::Any, 0, 0, false); }
            Node::Class(i) => { self.emit(Op::Class, *i, 0, false); }
            Node::Bol => { self.emit(Op::Bol, 0, 0, false); }
            Node::Eol => { self.emit(Op::Eol, 0, 0, false); }
            Node::Cat(v) => { for k in v { self.compile(k, level); } }
            Node::Group(g, k) => {
                self.emit(Op::Save, 2 * g, 0, false);
                self.compile(k, level);
                self.emit(Op::Save, 2 * g + 1, 0, false);
            }
            Node::Alt(v) => {
                if v.len() == 1 { self.compile(&v[0], level); return; }
                let mut jmps = Vec::new();
                for (i, k) in v.iter().enumerate() {
                    if i + 1 < v.len() {
                        let s = self.emit(Op::Split, 0, 0, false);
                        self.prog[s].a = self.prog.len();
                        self.compile(k, level);
                        jmps.push(self.emit(Op::Jmp, 0, 0, false));
                        self.prog[s].b = self.prog.len();
                    } else { self.compile(k, level); }
                }
                let end = self.prog.len();
                for j in jmps { self.prog[j].a = end; }
            }
            Node::Rep(k, mn, mx, greedy) => {
                for _ in 0..*mn { self.compile(k, level); }
                if *mx < 0 {
                    let l = self.emit(Op::Loop, 0, 0, *greedy);
                    self.prog[l].a = self.prog.len();
                    self.compile(k, level + 1);
                    let e = self.emit(Op::IterEnd, l, 0, false);
                    let end = self.prog.len();
                    self.prog[l].b = end;
                    self.prog[e].b = end;
                } else if (*mx as usize) > *mn {
                    let cnt = *mx as usize - *mn;
                    let mut loops = Vec::new();
                    let mut ends = Vec::new();
                    for _ in 0..cnt {
                        let l = self.emit(Op::Loop, 0, 0, *greedy);
                        self.prog[l].a = self.prog.len();
                        self.compile(k, level + 1);
                        let e = self.emit(Op::IterEnd, 0, 0, false);
                        self.prog[e].a = self.prog.len();
                        loops.push(l);
                        ends.push(e);
                    }
                    let end = self.prog.len();
                    for l in loops { self.prog[l].b = end; }
                    for e in ends { self.prog[e].b = end; }
                }
            }
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 { return; }
    let data = std::fs::read(format!("{}/cases.txt", args[1])).unwrap_or_default();
    let out = std::io::stdout();
    let mut w = std::io::BufWriter::new(out.lock());
    let mut lineno = 0;
    let mut memo: Vec<u8> = Vec::new();
    let lines: Vec<&[u8]> = data.split(|&b| b == b'\n').collect();
    let nlines = if data.last() == Some(&b'\n') { lines.len() - 1 } else { lines.len() };
    for li in 0..nlines {
        let line = lines[li];
        lineno += 1;
        let tab = match line.iter().position(|&b| b == b'\t') { Some(t) => t, None => continue };
        let pat = &line[..tab];
        let s = &line[tab + 1..];
        let n = s.len();
        let mut ps = Parser { p: pat, i: 0, ngroups: 0, classes: Vec::new() };
        let root = ps.regex();
        let mut cp = Comp { prog: Vec::new(), maxlevel: 0 };
        cp.compile(&root, 0);
        cp.emit(Op::Match, 0, 0, false);
        let prog = cp.prog;
        let classes = ps.classes;
        let ng = ps.ngroups;
        let dd = cp.maxlevel + 1;
        let msz = prog.len() * (n + 1) * dd;
        memo.clear();
        memo.resize(msz, 0);
        let ncap = 2 * (ng + 1);
        let mut caps = vec![-1i64; ncap];
        let mut found = false;
        let (mut ms, mut me) = (0usize, 0usize);
        let mut logs: Vec<(usize, i64)> = Vec::new();
        let mut stk: Vec<(usize, usize, usize, usize)> = Vec::new();
        for st in 0..=n {
            if found { break; }
            for c in caps.iter_mut() { *c = -1; }
            logs.clear();
            stk.clear();
            let (mut pc, mut pos, mut d) = (0usize, st, 0usize);
            loop {
                let key = (pc * (n + 1) + pos) * dd + d;
                let mut ok = true;
                if memo[key] != 0 { ok = false; } else {
                    memo[key] = 1;
                    let ins = prog[pc];
                    match ins.op {
                        Op::Char => { if pos < n && s[pos] as usize == ins.a { pos += 1; d = 0; pc += 1; } else { ok = false; } }
                        Op::Any => { if pos < n { pos += 1; d = 0; pc += 1; } else { ok = false; } }
                        Op::Class => { if pos < n && classes[ins.a][(s[pos] & 127) as usize] { pos += 1; d = 0; pc += 1; } else { ok = false; } }
                        Op::Bol => { if pos == 0 { pc += 1; } else { ok = false; } }
                        Op::Eol => { if pos == n { pc += 1; } else { ok = false; } }
                        Op::Save => { logs.push((ins.a, caps[ins.a])); caps[ins.a] = pos as i64; pc += 1; }
                        Op::Jmp => { pc = ins.a; }
                        Op::Split => { stk.push((ins.b, pos, d, logs.len())); pc = ins.a; }
                        Op::Loop => {
                            if ins.c { stk.push((ins.b, pos, d, logs.len())); pc = ins.a; d += 1; }
                            else { stk.push((ins.a, pos, d + 1, logs.len())); pc = ins.b; }
                        }
                        Op::IterEnd => { if d >= 1 { d -= 1; pc = ins.b; } else { pc = ins.a; } }
                        Op::Match => { found = true; ms = st; me = pos; }
                    }
                }
                if found { break; }
                if !ok {
                    match stk.pop() {
                        None => break,
                        Some((p2, q2, d2, lh)) => {
                            pc = p2; pos = q2; d = d2;
                            while logs.len() > lh { let (slot, old) = logs.pop().unwrap(); caps[slot] = old; }
                        }
                    }
                }
            }
        }
        if !found { writeln!(w, "{}: no match", lineno).unwrap(); }
        else {
            write!(w, "{}: {}-{}", lineno, ms, me).unwrap();
            for g in 1..=ng {
                if caps[2 * g] < 0 || caps[2 * g + 1] < 0 { write!(w, " -").unwrap(); }
                else { write!(w, " {}-{}", caps[2 * g], caps[2 * g + 1]).unwrap(); }
            }
            writeln!(w).unwrap();
        }
    }
}
