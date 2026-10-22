use std::collections::HashMap;
use std::fs;
use std::io::{self, Write};

#[derive(Clone)]
struct TType {
    off: i64,
    abbr: String,
}

struct Rule {
    m: i64,
    w: i64,
    d: i64,
    t: i64,
}

struct Zone {
    times: Vec<i64>,
    idx: Vec<usize>,
    types: Vec<TType>,
    has_footer: bool,
    fstd: TType,
    fdst: Option<TType>,
    start: Rule,
    end: Rule,
}

fn floordiv(a: i64, b: i64) -> i64 {
    a.div_euclid(b)
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = floordiv(y, 400);
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719468;
    let era = floordiv(z, 146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn month_len(y: i64, m: i64) -> i64 {
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    match m {
        2 => {
            if leap {
                29
            } else {
                28
            }
        }
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

fn rule_local(y: i64, r: &Rule) -> i64 {
    let first = days_from_civil(y, r.m, 1);
    let dow = (first + 4).rem_euclid(7);
    let mut day = 1 + (r.d - dow + 7) % 7 + (r.w - 1) * 7;
    let ml = month_len(y, r.m);
    while day > ml {
        day -= 7;
    }
    (first + day - 1) * 86400 + r.t
}

fn year_of(t: i64) -> i64 {
    civil_from_days(floordiv(t, 86400)).0
}

struct P<'a> {
    s: &'a [u8],
    i: usize,
}

impl<'a> P<'a> {
    fn peek(&self) -> u8 {
        if self.i < self.s.len() {
            self.s[self.i]
        } else {
            0
        }
    }
    fn name(&mut self) -> String {
        let mut out = Vec::new();
        if self.peek() == b'<' {
            self.i += 1;
            while self.peek() != 0 && self.peek() != b'>' {
                out.push(self.peek());
                self.i += 1;
            }
            if self.peek() == b'>' {
                self.i += 1;
            }
        } else {
            while self.peek().is_ascii_alphabetic() {
                out.push(self.peek());
                self.i += 1;
            }
        }
        String::from_utf8_lossy(&out).into_owned()
    }
    fn num(&mut self) -> i64 {
        let mut v = 0;
        while self.peek().is_ascii_digit() {
            v = v * 10 + (self.peek() - b'0') as i64;
            self.i += 1;
        }
        v
    }
    fn hms(&mut self) -> i64 {
        let mut sign = 1;
        if self.peek() == b'+' {
            self.i += 1;
        } else if self.peek() == b'-' {
            sign = -1;
            self.i += 1;
        }
        let h = self.num();
        let mut m = 0;
        let mut s = 0;
        if self.peek() == b':' {
            self.i += 1;
            m = self.num();
            if self.peek() == b':' {
                self.i += 1;
                s = self.num();
            }
        }
        sign * (h * 3600 + m * 60 + s)
    }
    fn rule(&mut self, r: &mut Rule) {
        r.t = 7200;
        if self.peek() == b'M' {
            self.i += 1;
            r.m = self.num();
            if self.peek() == b'.' {
                self.i += 1;
            }
            r.w = self.num();
            if self.peek() == b'.' {
                self.i += 1;
            }
            r.d = self.num();
        }
        if self.peek() == b'/' {
            self.i += 1;
            r.t = self.hms();
        }
    }
}

fn be(b: &[u8], p: usize, n: usize) -> i64 {
    let mut v: u64 = 0;
    for k in 0..n {
        v = (v << 8) | b[p + k] as u64;
    }
    if n == 4 {
        v as u32 as i32 as i64
    } else {
        v as i64
    }
}

impl Zone {
    fn load(path: &str) -> Option<Zone> {
        let b = fs::read(path).ok()?;
        let cnt = |p: usize| -> Vec<i64> { (0..6).map(|i| be(&b, p + 20 + 4 * i, 4)).collect() };
        let c = cnt(0);
        let v1 = c[3] * 5 + c[4] * 6 + c[5] + c[2] * 8 + c[1] + c[0];
        let mut p = 44 + v1 as usize;
        let c = cnt(p);
        let (isut, isstd, leap, tc, ty, ch) = (
            c[0] as usize,
            c[1] as usize,
            c[2] as usize,
            c[3] as usize,
            c[4] as usize,
            c[5] as usize,
        );
        p += 44;
        let times: Vec<i64> = (0..tc).map(|i| be(&b, p + 8 * i, 8)).collect();
        p += 8 * tc;
        let idx: Vec<usize> = (0..tc).map(|i| b[p + i] as usize).collect();
        p += tc;
        let tp = p;
        p += 6 * ty;
        let chars = &b[p..p + ch];
        let types: Vec<TType> = (0..ty)
            .map(|i| {
                let off = be(&b, tp + 6 * i, 4);
                let ai = b[tp + 6 * i + 5] as usize;
                let mut e = ai;
                while e < ch && chars[e] != 0 {
                    e += 1;
                }
                TType {
                    off,
                    abbr: String::from_utf8_lossy(&chars[ai.min(ch)..e]).into_owned(),
                }
            })
            .collect();
        p += ch + leap * 12 + isstd + isut;
        let mut foot = Vec::new();
        if p < b.len() && b[p] == b'\n' {
            p += 1;
            while p < b.len() && b[p] != b'\n' {
                foot.push(b[p]);
                p += 1;
            }
        }
        let mut z = Zone {
            times,
            idx,
            types,
            has_footer: !foot.is_empty(),
            fstd: TType { off: 0, abbr: String::new() },
            fdst: None,
            start: Rule { m: 3, w: 2, d: 0, t: 7200 },
            end: Rule { m: 11, w: 1, d: 0, t: 7200 },
        };
        if z.has_footer {
            let mut ps = P { s: &foot, i: 0 };
            let n = ps.name();
            let o = ps.hms();
            z.fstd = TType { off: -o, abbr: n };
            if ps.peek() != 0 && ps.peek() != b',' {
                let dn = ps.name();
                let doff = if ps.peek() != 0 && ps.peek() != b',' {
                    -ps.hms()
                } else {
                    z.fstd.off + 3600
                };
                z.fdst = Some(TType { off: doff, abbr: dn });
                if ps.peek() == b',' {
                    ps.i += 1;
                    ps.rule(&mut z.start);
                    if ps.peek() == b',' {
                        ps.i += 1;
                        ps.rule(&mut z.end);
                    }
                }
            }
        }
        Some(z)
    }

    fn events(&self, y: i64) -> (i64, i64) {
        let d = self.fdst.as_ref().unwrap();
        (
            rule_local(y, &self.start) - self.fstd.off,
            rule_local(y, &self.end) - d.off,
        )
    }

    fn footer_type(&self, t: i64) -> &TType {
        let d = match &self.fdst {
            None => return &self.fstd,
            Some(d) => d,
        };
        let yy = year_of(t);
        let mut best: Option<(i64, bool)> = None;
        for y in yy - 2..=yy + 2 {
            let (st, en) = self.events(y);
            if st <= t && best.map_or(true, |b| st > b.0) {
                best = Some((st, true));
            }
            if en <= t && best.map_or(true, |b| en > b.0) {
                best = Some((en, false));
            }
        }
        if best.map_or(false, |b| b.1) {
            d
        } else {
            &self.fstd
        }
    }

    fn type_at(&self, t: i64) -> &TType {
        let n = self.times.len();
        if n == 0 {
            return if self.has_footer { self.footer_type(t) } else { &self.types[0] };
        }
        if t < self.times[0] {
            return &self.types[0];
        }
        if t > self.times[n - 1] {
            return if self.has_footer {
                self.footer_type(t)
            } else {
                &self.types[self.idx[n - 1]]
            };
        }
        let k = self.times.partition_point(|&x| x <= t) - 1;
        &self.types[self.idx[k]]
    }
}

fn fmt_off(off: i64) -> String {
    let sign = if off < 0 { '-' } else { '+' };
    let a = off.abs();
    if a % 60 != 0 {
        format!("{}{:02}:{:02}:{:02}", sign, a / 3600, (a / 60) % 60, a % 60)
    } else {
        format!("{}{:02}:{:02}", sign, a / 3600, (a / 60) % 60)
    }
}

fn fmt_local(t: i64, off: i64) -> String {
    let l = t + off;
    let (y, m, d) = civil_from_days(floordiv(l, 86400));
    let s = l.rem_euclid(86400);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}{}",
        y,
        m,
        d,
        s / 3600,
        (s / 60) % 60,
        s % 60,
        fmt_off(off)
    )
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dir = &args[1];
    let q = fs::read_to_string(format!("{}/queries.txt", dir)).unwrap_or_default();
    let mut zones: HashMap<String, Option<Zone>> = HashMap::new();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for raw in q.lines() {
        let line = raw.trim_end_matches('\r');
        if line.trim().is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.split(' ').collect();
        if parts.len() < 3 {
            continue;
        }
        let zo = zones
            .entry(parts[1].to_string())
            .or_insert_with(|| Zone::load(&format!("{}/{}", dir, parts[1])));
        let z = match zo {
            Some(z) => z,
            None => continue,
        };
        if parts[0] == "UTC" {
            let t: i64 = parts[2].parse().unwrap();
            let tt = z.type_at(t);
            writeln!(out, "{} -> {} {}", line, fmt_local(t, tt.off), tt.abbr).unwrap();
        } else {
            let a = parts[2].as_bytes();
            let num = |s: usize, e: usize| -> i64 { std::str::from_utf8(&a[s..e]).unwrap().parse().unwrap() };
            let w = days_from_civil(num(0, 4), num(5, 7), num(8, 10)) * 86400
                + num(11, 13) * 3600
                + num(14, 16) * 60
                + num(17, 19);
            let mut offs: Vec<i64> = z.types.iter().map(|t| t.off).collect();
            if z.has_footer {
                offs.push(z.fstd.off);
                if let Some(d) = &z.fdst {
                    offs.push(d.off);
                }
            }
            let minoff = *offs.iter().min().unwrap();
            let maxoff = *offs.iter().max().unwrap();
            let mut res: Vec<i64> = Vec::new();
            for &o in &offs {
                let t = w - o;
                if z.type_at(t).off == o && !res.contains(&t) {
                    res.push(t);
                }
            }
            if !res.is_empty() {
                res.sort();
                let s: Vec<String> = res
                    .iter()
                    .map(|&t| {
                        let tt = z.type_at(t);
                        format!("{} {} {}", t, fmt_off(tt.off), tt.abbr)
                    })
                    .collect();
                writeln!(out, "{} -> {}", line, s.join(" | ")).unwrap();
            } else {
                let lo = w - maxoff - 2;
                let hi = w - minoff + 2;
                let mut c: Vec<i64> = z.times.iter().cloned().filter(|&x| x >= lo && x <= hi).collect();
                let last_t = if z.times.is_empty() { i64::MIN / 2 } else { *z.times.last().unwrap() };
                if !z.times.is_empty() && last_t + 1 >= lo && last_t + 1 <= hi {
                    c.push(last_t + 1);
                }
                if z.fdst.is_some() {
                    for y in year_of(lo) - 1..=year_of(hi) + 1 {
                        let (st, en) = z.events(y);
                        for x in [st, en] {
                            if x > last_t && x >= lo && x <= hi {
                                c.push(x);
                            }
                        }
                    }
                }
                c.sort();
                let mut t = w - z.type_at(w).off;
                for &tr in &c {
                    let ob = z.type_at(tr - 1).off;
                    let oa = z.type_at(tr).off;
                    if tr + ob <= w && w < tr + oa {
                        t = w - ob;
                        break;
                    }
                }
                let tt = z.type_at(t);
                writeln!(out, "{} -> GAP {} {} {}", line, t, fmt_local(t, tt.off), tt.abbr).unwrap();
            }
        }
    }
}
