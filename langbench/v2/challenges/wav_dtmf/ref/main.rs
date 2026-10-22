use std::fmt::Write as _;
use std::io::Write;

fn u16le(b: &[u8], p: usize) -> u32 { b[p] as u32 | (b[p + 1] as u32) << 8 }
fn u32le(b: &[u8], p: usize) -> u32 { u16le(b, p) | u16le(b, p + 2) << 16 }

const LOWF: [f64; 4] = [697.0, 770.0, 852.0, 941.0];
const HIGHF: [f64; 4] = [1209.0, 1336.0, 1477.0, 1633.0];
const KEYS: [[u8; 4]; 4] = [*b"123A", *b"456B", *b"789C", *b"*0#D"];

fn process(name: &str, buf: &[u8], out: &mut String) {
    let len = buf.len();
    let mut fmt: Option<&[u8]> = None;
    let mut data: Option<&[u8]> = None;
    let mut pos = 12usize;
    while pos + 8 <= len {
        let n = u32le(buf, pos + 4) as usize;
        let start = pos + 8;
        let end = (start + n).min(len);
        let id = &buf[pos..pos + 4];
        if id == b"fmt " { fmt = Some(&buf[start..end]); }
        else if id == b"data" { data = Some(&buf[start..end]); }
        pos = start + n + (n & 1);
    }
    let fmt = match fmt { Some(f) if f.len() >= 16 => f, _ => { writeln!(out, "{}: unsupported", name).unwrap(); return; } };
    let data = data.unwrap_or(&[]);
    let tag = u16le(fmt, 0);
    let ch = u16le(fmt, 2) as usize;
    let rate = u32le(fmt, 4) as usize;
    let bits = u16le(fmt, 14);
    let eff = if tag == 0xFFFE { if fmt.len() >= 26 { u16le(fmt, 24) } else { 0 } } else { tag };
    if eff != 1 || bits != 16 { writeln!(out, "{}: unsupported", name).unwrap(); return; }
    let frames = data.len() / (2 * ch);
    let n = rate / 50;
    let nb = frames / n;
    writeln!(out, "{}: {} Hz, {} ch, {} blocks", name, rate, ch, nb).unwrap();
    let mut lab = vec![0u8; nb];
    let mut x = vec![0f64; n];
    for b in 0..nb {
        let mut e = 0f64;
        for i in 0..n {
            let off = (b * n + i) * 2 * ch;
            let s = i16::from_le_bytes([data[off], data[off + 1]]) as f64;
            x[i] = s;
            e += s * s;
        }
        if e < 1000000.0 * n as f64 { continue; }
        let mut r = [0f64; 8];
        for k in 0..8 {
            let fq = if k < 4 { LOWF[k] } else { HIGHF[k - 4] };
            let c = 2.0 * (2.0 * std::f64::consts::PI * fq / rate as f64).cos();
            let (mut s1, mut s2) = (0f64, 0f64);
            for i in 0..n { let s = x[i] + c * s1 - s2; s2 = s1; s1 = s; }
            let p = s1 * s1 + s2 * s2 - c * s1 * s2;
            r[k] = 2.0 * p / (n as f64 * e);
        }
        let mut bl = 0; let mut bh = 0;
        for k in 1..4 { if r[k] > r[bl] { bl = k; } if r[4 + k] > r[4 + bh] { bh = k; } }
        if r[bl] >= 0.1 && r[4 + bh] >= 0.1 && r[bl] + r[4 + bh] >= 0.6 { lab[b] = KEYS[bl][bh]; }
    }
    let mut br = lab.clone();
    for b in 1..nb.saturating_sub(1) {
        if lab[b] == 0 && lab[b - 1] != 0 && lab[b - 1] == lab[b + 1] { br[b] = lab[b - 1]; }
    }
    let mut digits = String::new();
    let mut i = 0;
    while i < nb {
        let mut j = i;
        while j + 1 < nb && br[j + 1] == br[i] { j += 1; }
        if br[i] != 0 && j - i + 1 >= 2 {
            writeln!(out, "{} {} {}", br[i] as char, i * 20, (j + 1) * 20).unwrap();
            digits.push(br[i] as char);
        }
        i = j + 1;
    }
    writeln!(out, "digits: {}", digits).unwrap();
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 { return; }
    let dir = std::path::Path::new(&args[1]);
    let mut names: Vec<Vec<u8>> = Vec::new();
    let mut map = std::collections::HashMap::new();
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            let os = e.file_name();
            let s = os.to_string_lossy().into_owned();
            if s.ends_with(".wav") {
                use std::os::unix::ffi::OsStrExt;
                let bytes = os.as_bytes().to_vec();
                map.insert(bytes.clone(), (s, e.path()));
                names.push(bytes);
            }
        }
    }
    names.sort();
    let mut out = String::new();
    for (k, nm) in names.iter().enumerate() {
        if k > 0 { out.push('\n'); }
        let (s, p) = &map[nm];
        let buf = std::fs::read(p).unwrap_or_default();
        process(s, &buf, &mut out);
    }
    std::io::stdout().write_all(out.as_bytes()).unwrap();
}
