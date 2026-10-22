use std::cmp::Ordering;
use std::collections::{BTreeMap, HashMap};
use std::io::Write;

const BASE: u64 = 1_000_000_000;

// magnitude helpers (little-endian base 1e9, no trailing zero limbs)
fn trim(a: &mut Vec<u32>) { while let Some(&0) = a.last() { a.pop(); } }
fn mcmp(a: &[u32], b: &[u32]) -> Ordering {
    if a.len() != b.len() { return a.len().cmp(&b.len()); }
    for i in (0..a.len()).rev() { if a[i] != b[i] { return a[i].cmp(&b[i]); } }
    Ordering::Equal
}
fn madd(a: &[u32], b: &[u32]) -> Vec<u32> {
    let n = a.len().max(b.len());
    let mut r = Vec::with_capacity(n + 1);
    let mut c = 0u64;
    for i in 0..n {
        let s = c + *a.get(i).unwrap_or(&0) as u64 + *b.get(i).unwrap_or(&0) as u64;
        r.push((s % BASE) as u32); c = s / BASE;
    }
    if c > 0 { r.push(c as u32); }
    r
}
fn msub(a: &[u32], b: &[u32]) -> Vec<u32> { // a >= b
    let mut r = Vec::with_capacity(a.len());
    let mut br = 0i64;
    for i in 0..a.len() {
        let mut s = a[i] as i64 - br - *b.get(i).unwrap_or(&0) as i64;
        if s < 0 { s += BASE as i64; br = 1; } else { br = 0; }
        r.push(s as u32);
    }
    trim(&mut r); r
}
fn mmul(a: &[u32], b: &[u32]) -> Vec<u32> {
    if a.is_empty() || b.is_empty() { return vec![]; }
    let mut r = vec![0u64; a.len() + b.len() + 1];
    for i in 0..a.len() {
        let mut c = 0u64;
        for j in 0..b.len() {
            let t = r[i + j] + a[i] as u64 * b[j] as u64 + c;
            r[i + j] = t % BASE; c = t / BASE;
        }
        let mut k = i + b.len();
        while c > 0 { let t = r[k] + c; r[k] = t % BASE; c = t / BASE; k += 1; }
    }
    let mut v: Vec<u32> = r.into_iter().map(|x| x as u32).collect();
    trim(&mut v); v
}
fn mfrom_digits(s: &str) -> Vec<u32> {
    let s = s.trim_start_matches('0');
    let b = s.as_bytes();
    let mut r = vec![];
    let mut end = b.len();
    while end > 0 {
        let st = if end >= 9 { end - 9 } else { 0 };
        r.push(std::str::from_utf8(&b[st..end]).unwrap().parse::<u32>().unwrap());
        end = st;
    }
    trim(&mut r); r
}
fn mto_str(a: &[u32]) -> String {
    if a.is_empty() { return "0".into(); }
    let mut s = format!("{}", a[a.len() - 1]);
    for i in (0..a.len() - 1).rev() { s.push_str(&format!("{:09}", a[i])); }
    s
}
fn mdivmod(a: &[u32], b: &[u32]) -> (Vec<u32>, Vec<u32>) {
    let ds = mto_str(a);
    let mut q = String::new();
    let mut rem: Vec<u32> = vec![];
    let ten = vec![10u32];
    for ch in ds.bytes() {
        rem = madd(&mmul(&rem, &ten), &mfrom_digits(&((ch - b'0') as u32).to_string()));
        let mut d = 0u8;
        while mcmp(&rem, b) != Ordering::Less { rem = msub(&rem, b); d += 1; }
        q.push((b'0' + d) as char);
    }
    (mfrom_digits(&q), rem)
}

#[derive(Clone, Debug)]
struct Big { neg: bool, m: Vec<u32> }
impl Big {
    fn zero() -> Big { Big { neg: false, m: vec![] } }
    fn norm(mut self) -> Big { trim(&mut self.m); if self.m.is_empty() { self.neg = false; } self }
    fn is_zero(&self) -> bool { self.m.is_empty() }
    fn sign(&self) -> i32 { if self.m.is_empty() { 0 } else if self.neg { -1 } else { 1 } }
    fn negate(&self) -> Big { Big { neg: !self.neg, m: self.m.clone() }.norm() }
    fn add(&self, o: &Big) -> Big {
        if self.neg == o.neg { return Big { neg: self.neg, m: madd(&self.m, &o.m) }.norm(); }
        match mcmp(&self.m, &o.m) {
            Ordering::Less => Big { neg: o.neg, m: msub(&o.m, &self.m) }.norm(),
            _ => Big { neg: self.neg, m: msub(&self.m, &o.m) }.norm(),
        }
    }
    fn sub(&self, o: &Big) -> Big { self.add(&o.negate()) }
    fn mul(&self, o: &Big) -> Big { Big { neg: self.neg != o.neg, m: mmul(&self.m, &o.m) }.norm() }
    fn cmp(&self, o: &Big) -> Ordering {
        let (a, b) = (self.sign(), o.sign());
        if a != b { return a.cmp(&b); }
        let c = mcmp(&self.m, &o.m);
        if self.neg { c.reverse() } else { c }
    }
}

struct Dec { v: Big, s: usize }
fn parse(t: &str) -> Dec {
    let (neg, t) = if let Some(r) = t.strip_prefix('-') { (true, r) } else { (false, t) };
    let (ip, fp) = match t.find('.') { Some(i) => (&t[..i], &t[i + 1..]), None => (t, "") };
    let mut digits = String::from(ip); digits.push_str(fp);
    Dec { v: Big { neg, m: mfrom_digits(&digits) }.norm(), s: fp.len() }
}
fn rnd(d: &Dec, m: usize) -> Big {
    let mut s = mto_str(&d.v.m);
    if d.s <= m {
        for _ in 0..(m - d.s) { s.push('0'); }
        return Big { neg: d.v.neg, m: mfrom_digits(&s) }.norm();
    }
    let k = d.s - m;
    while s.len() <= k { s.insert(0, '0'); }
    let (q, tail) = s.split_at(s.len() - k);
    let half = format!("5{}", "0".repeat(k - 1));
    let mut qm = mfrom_digits(q);
    let odd = (q.as_bytes()[q.len() - 1] - b'0') % 2 == 1;
    let c = tail.cmp(&half[..]);
    if c == Ordering::Greater || (c == Ordering::Equal && odd) { qm = madd(&qm, &[1]); }
    Big { neg: d.v.neg, m: qm }.norm()
}
fn fmt(v: &Big, m: usize) -> String {
    let mut s = mto_str(&v.m);
    while s.len() <= m { s.insert(0, '0'); }
    let (ip, fp) = s.split_at(s.len() - m);
    let mut g = String::new();
    let n = ip.len();
    for (i, ch) in ip.chars().enumerate() {
        if i > 0 && (n - i) % 3 == 0 { g.push(','); }
        g.push(ch);
    }
    let mut r = String::new();
    if v.sign() < 0 { r.push('-'); }
    r.push_str(&g);
    if m > 0 { r.push('.'); r.push_str(fp); }
    r
}

struct Acct { cur: String, bal: Big, limit: Big }
struct L { curs: HashMap<String, usize>, accts: BTreeMap<String, Acct> }

impl L {
    fn unknown(&self, names: &[&str]) -> Option<String> {
        for n in names { if !self.accts.contains_key(*n) { return Some(format!("unknown account {}", n)); } }
        None
    }
    fn insufficient(&self, n: &str, amt: &Big) -> bool {
        let a = &self.accts[n];
        a.bal.sub(amt).cmp(&a.limit.negate()) == Ordering::Less
    }
    fn m(&self, n: &str) -> usize { self.curs[&self.accts[n].cur] }
    fn exec(&mut self, f: &[&str]) -> Option<String> {
        let cmd = f[0];
        match cmd {
            "currency" => { self.curs.insert(f[1].to_string(), f[2].parse().unwrap()); None }
            "open" => {
                if self.accts.contains_key(f[1]) { return Some(format!("account exists {}", f[1])); }
                let m = self.curs[f[2]];
                let limit = if f.len() > 3 { rnd(&parse(f[3]), m) } else { Big::zero() };
                self.accts.insert(f[1].to_string(), Acct { cur: f[2].to_string(), bal: Big::zero(), limit });
                None
            }
            "deposit" | "withdraw" => {
                if let Some(u) = self.unknown(&[f[1]]) { return Some(u); }
                let amt = rnd(&parse(f[2]), self.m(f[1]));
                if amt.sign() <= 0 { return Some("non-positive amount".into()); }
                if cmd == "withdraw" {
                    if self.insufficient(f[1], &amt) { return Some(format!("insufficient funds {}", f[1])); }
                    let a = self.accts.get_mut(f[1]).unwrap(); a.bal = a.bal.sub(&amt);
                } else {
                    let a = self.accts.get_mut(f[1]).unwrap(); a.bal = a.bal.add(&amt);
                }
                None
            }
            "transfer" => {
                if let Some(u) = self.unknown(&[f[1], f[2]]) { return Some(u); }
                if self.accts[f[1]].cur != self.accts[f[2]].cur { return Some("currency mismatch".into()); }
                let amt = rnd(&parse(f[3]), self.m(f[1]));
                if amt.sign() <= 0 { return Some("non-positive amount".into()); }
                if self.insufficient(f[1], &amt) { return Some(format!("insufficient funds {}", f[1])); }
                let a = self.accts.get_mut(f[1]).unwrap(); a.bal = a.bal.sub(&amt);
                let b = self.accts.get_mut(f[2]).unwrap(); b.bal = b.bal.add(&amt);
                None
            }
            "convert" => {
                if let Some(u) = self.unknown(&[f[1], f[2]]) { return Some(u); }
                let (ma, mb) = (self.m(f[1]), self.m(f[2]));
                let amt = rnd(&parse(f[3]), ma);
                if amt.sign() <= 0 { return Some("non-positive amount".into()); }
                if self.insufficient(f[1], &amt) { return Some(format!("insufficient funds {}", f[1])); }
                let r = parse(f[4]);
                let got = rnd(&Dec { v: amt.mul(&r.v), s: ma + r.s }, mb);
                let a = self.accts.get_mut(f[1]).unwrap(); a.bal = a.bal.sub(&amt);
                let b = self.accts.get_mut(f[2]).unwrap(); b.bal = b.bal.add(&got);
                None
            }
            "interest" => {
                if let Some(u) = self.unknown(&[f[1]]) { return Some(u); }
                let m = self.m(f[1]);
                let r = parse(f[2]);
                let a = self.accts.get_mut(f[1]).unwrap();
                let add = rnd(&Dec { v: a.bal.mul(&r.v), s: m + r.s }, m);
                a.bal = a.bal.add(&add);
                None
            }
            "split" => {
                let mut names: Vec<&str> = vec![f[1]];
                let mut w: Vec<Vec<u32>> = vec![];
                for t in &f[3..] {
                    let c = t.rfind(':').unwrap();
                    names.push(&t[..c]);
                    w.push(mfrom_digits(&t[c + 1..]));
                }
                if let Some(u) = self.unknown(&names) { return Some(u); }
                let cur = self.accts[f[1]].cur.clone();
                for n in &names[1..] { if self.accts[*n].cur != cur { return Some("currency mismatch".into()); } }
                let amt = rnd(&parse(f[2]), self.m(f[1]));
                if amt.sign() <= 0 { return Some("non-positive amount".into()); }
                if self.insufficient(f[1], &amt) { return Some(format!("insufficient funds {}", f[1])); }
                let am = amt.m.clone();
                let mut wsum: Vec<u32> = vec![];
                for x in &w { wsum = madd(&wsum, x); }
                let mut q = vec![]; let mut rem = vec![];
                let mut given: Vec<u32> = vec![];
                for x in &w {
                    let (qq, rr) = mdivmod(&mmul(&am, x), &wsum);
                    given = madd(&given, &qq);
                    q.push(qq); rem.push(rr);
                }
                let left: u64 = mto_str(&msub(&am, &given)).parse().unwrap();
                let mut idx: Vec<usize> = (0..w.len()).collect();
                idx.sort_by(|&x, &y| mcmp(&rem[y], &rem[x]).then(x.cmp(&y)));
                for j in 0..left as usize { let i = idx[j]; q[i] = madd(&q[i], &[1]); }
                let a = self.accts.get_mut(f[1]).unwrap(); a.bal = a.bal.sub(&amt);
                for (i, n) in names[1..].iter().enumerate() {
                    let t = self.accts.get_mut(*n).unwrap();
                    t.bal = t.bal.add(&Big { neg: false, m: q[i].clone() }.norm());
                }
                None
            }
            _ => None,
        }
    }
}

fn main() {
    let dir = std::env::args().nth(1).unwrap();
    let text = std::fs::read_to_string(std::path::Path::new(&dir).join("ledger.txt")).unwrap();
    let mut l = L { curs: HashMap::new(), accts: BTreeMap::new() };
    let mut out = String::new();
    for (i, raw) in text.lines().enumerate() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') { continue; }
        let f: Vec<&str> = line.split(' ').filter(|s| !s.is_empty()).collect();
        if let Some(r) = l.exec(&f) { out.push_str(&format!("line {}: REJECT {}\n", i + 1, r)); }
    }
    out.push('\n');
    let mut tot: BTreeMap<String, (Big, usize)> = BTreeMap::new();
    for (n, a) in &l.accts {
        let m = l.curs[&a.cur];
        out.push_str(&format!("{} {} {}\n", n, a.cur, fmt(&a.bal, m)));
        let e = tot.entry(a.cur.clone()).or_insert((Big::zero(), 0));
        e.0 = e.0.add(&a.bal); e.1 += 1;
    }
    out.push('\n');
    for (c, (s, k)) in &tot {
        out.push_str(&format!("total {} {} ({} accounts)\n", c, fmt(s, l.curs[c]), k));
    }
    std::io::stdout().write_all(out.as_bytes()).unwrap();
}
