use std::fs;
use std::io::Write;

fn crc_table() -> [u32; 256] {
    let mut t = [0u32; 256];
    for n in 0..256u32 {
        let mut c = n;
        for _ in 0..8 {
            c = if c & 1 != 0 { 0xEDB88320 ^ (c >> 1) } else { c >> 1 };
        }
        t[n as usize] = c;
    }
    t
}

fn crc_update(t: &[u32; 256], mut c: u32, b: &[u8]) -> u32 {
    for &x in b {
        c = t[((c ^ x as u32) & 0xff) as usize] ^ (c >> 8);
    }
    c
}

fn be32(b: &[u8], p: usize) -> u32 {
    ((b[p] as u32) << 24) | ((b[p + 1] as u32) << 16) | ((b[p + 2] as u32) << 8) | b[p + 3] as u32
}

struct Inf<'a> {
    inp: &'a [u8],
    pos: usize,
    bitbuf: u32,
    bitcnt: u32,
    out: Vec<u8>,
    nstored: u32,
    nfixed: u32,
    ndynamic: u32,
}

struct Huff {
    count: [i32; 16],
    sym: Vec<i32>,
}

fn build(len: &[u8]) -> Huff {
    let mut count = [0i32; 16];
    for &l in len {
        count[l as usize] += 1;
    }
    count[0] = 0;
    let mut offs = [0i32; 16];
    for i in 1..15 {
        offs[i + 1] = offs[i] + count[i];
    }
    let mut sym = vec![0i32; len.len().max(1)];
    for (i, &l) in len.iter().enumerate() {
        if l != 0 {
            sym[offs[l as usize] as usize] = i as i32;
            offs[l as usize] += 1;
        }
    }
    Huff { count, sym }
}

const LBASE: [u32; 29] = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
const LEXT: [u32; 29] = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
const DBASE: [u32; 30] = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577];
const DEXT: [u32; 30] = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];

impl<'a> Inf<'a> {
    fn bit(&mut self) -> u32 {
        if self.bitcnt == 0 {
            self.bitbuf = if self.pos < self.inp.len() { self.inp[self.pos] as u32 } else { 0 };
            self.pos += 1;
            self.bitcnt = 8;
        }
        let b = self.bitbuf & 1;
        self.bitbuf >>= 1;
        self.bitcnt -= 1;
        b
    }
    fn bits(&mut self, n: u32) -> u32 {
        let mut v = 0;
        for i in 0..n {
            v |= self.bit() << i;
        }
        v
    }
    fn decode(&mut self, h: &Huff) -> i32 {
        let (mut code, mut first, mut index) = (0i32, 0i32, 0i32);
        for len in 1..16 {
            code |= self.bit() as i32;
            let count = h.count[len];
            if code - count < first {
                return h.sym[(index + code - first) as usize];
            }
            index += count;
            first += count;
            first <<= 1;
            code <<= 1;
        }
        -1
    }
    fn codes(&mut self, lc: &Huff, dc: &Huff) -> bool {
        loop {
            let sym = self.decode(lc);
            if sym < 0 {
                return false;
            }
            if sym < 256 {
                self.out.push(sym as u8);
            } else if sym == 256 {
                return true;
            } else {
                let s = (sym - 257) as usize;
                if s >= 29 {
                    return false;
                }
                let len = LBASE[s] + self.bits(LEXT[s]);
                let d = self.decode(dc);
                if d < 0 || d >= 30 {
                    return false;
                }
                let d = d as usize;
                let dist = (DBASE[d] + self.bits(DEXT[d])) as usize;
                if dist > self.out.len() {
                    return false;
                }
                for _ in 0..len {
                    let b = self.out[self.out.len() - dist];
                    self.out.push(b);
                }
            }
        }
    }
    fn inflate(&mut self) -> bool {
        loop {
            let last = self.bit();
            let t = self.bits(2);
            if t == 0 {
                self.nstored += 1;
                self.bitcnt = 0;
                if self.pos + 4 > self.inp.len() {
                    return false;
                }
                let len = self.inp[self.pos] as usize | ((self.inp[self.pos + 1] as usize) << 8);
                self.pos += 4;
                if self.pos + len > self.inp.len() {
                    return false;
                }
                let sl = &self.inp[self.pos..self.pos + len];
                self.out.extend_from_slice(sl);
                self.pos += len;
            } else if t == 1 {
                self.nfixed += 1;
                let mut l = [0u8; 288];
                for i in 0..288 {
                    l[i] = if i < 144 { 8 } else if i < 256 { 9 } else if i < 280 { 7 } else { 8 };
                }
                let lc = build(&l);
                let dc = build(&[5u8; 30]);
                if !self.codes(&lc, &dc) {
                    return false;
                }
            } else if t == 2 {
                self.ndynamic += 1;
                let nlen = self.bits(5) as usize + 257;
                let ndist = self.bits(5) as usize + 1;
                let ncode = self.bits(4) as usize + 4;
                const ORD: [usize; 19] = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];
                let mut cl = [0u8; 19];
                for i in 0..ncode {
                    cl[ORD[i]] = self.bits(3) as u8;
                }
                let ch = build(&cl);
                let mut lens = vec![0u8; nlen + ndist];
                let mut idx = 0;
                while idx < nlen + ndist {
                    let sym = self.decode(&ch);
                    if sym < 0 {
                        return false;
                    }
                    if sym < 16 {
                        lens[idx] = sym as u8;
                        idx += 1;
                    } else {
                        let (rep, val) = if sym == 16 {
                            if idx == 0 {
                                return false;
                            }
                            (3 + self.bits(2) as usize, lens[idx - 1])
                        } else if sym == 17 {
                            (3 + self.bits(3) as usize, 0)
                        } else {
                            (11 + self.bits(7) as usize, 0)
                        };
                        if idx + rep > nlen + ndist {
                            return false;
                        }
                        for _ in 0..rep {
                            lens[idx] = val;
                            idx += 1;
                        }
                    }
                }
                let lc = build(&lens[..nlen]);
                let dc = build(&lens[nlen..]);
                if !self.codes(&lc, &dc) {
                    return false;
                }
            } else {
                return false;
            }
            if last == 1 {
                return true;
            }
        }
    }
}

fn paeth(a: i32, b: i32, c: i32) -> i32 {
    let p = a + b - c;
    let (pa, pb, pc) = ((p - a).abs(), (p - b).abs(), (p - c).abs());
    if pa <= pb && pa <= pc {
        a
    } else if pb <= pc {
        b
    } else {
        c
    }
}

fn process(t: &[u32; 256], dir: &str, name: &str, o: &mut String) {
    o.push_str(&format!("== {}\n", name));
    let buf = match fs::read(format!("{}/{}", dir, name)) {
        Ok(b) => b,
        Err(_) => return,
    };
    let sz = buf.len();
    let mut p = 8;
    let mut n = 0;
    while p + 12 <= sz {
        let len = be32(&buf, p) as usize;
        n += 1;
        let c = crc_update(t, 0xffffffff, &buf[p + 4..p + 8 + len]) ^ 0xffffffff;
        if c != be32(&buf, p + 8 + len) {
            o.push_str(&format!(
                "error: CRC mismatch in chunk {} {}\n",
                n,
                String::from_utf8_lossy(&buf[p + 4..p + 8])
            ));
            return;
        }
        p += 12 + len;
    }
    let (mut w, mut h, mut depth, mut ctype) = (0usize, 0usize, 0u32, 0u32);
    let mut pal: Vec<u8> = Vec::new();
    let mut trns: Vec<u8> = Vec::new();
    let mut hastrns = false;
    let mut idat: Vec<u8> = Vec::new();
    let mut types: Vec<(String, u32)> = Vec::new();
    p = 8;
    while p + 12 <= sz {
        let len = be32(&buf, p) as usize;
        let ty = String::from_utf8_lossy(&buf[p + 4..p + 8]).to_string();
        let d = &buf[p + 8..p + 8 + len];
        match types.last_mut() {
            Some((tt, k)) if *tt == ty => *k += 1,
            _ => types.push((ty.clone(), 1)),
        }
        match ty.as_str() {
            "IHDR" => {
                w = be32(d, 0) as usize;
                h = be32(d, 4) as usize;
                depth = d[8] as u32;
                ctype = d[9] as u32;
            }
            "PLTE" => pal = d.to_vec(),
            "tRNS" => {
                hastrns = true;
                trns = d.to_vec();
            }
            "IDAT" => idat.extend_from_slice(d),
            _ => {}
        }
        p += 12 + len;
    }
    let chunkline: Vec<String> = types
        .iter()
        .map(|(t, k)| if *k > 1 { format!("{}*{}", t, k) } else { t.clone() })
        .collect();

    let mut s = Inf { inp: &idat, pos: 2, bitbuf: 0, bitcnt: 0, out: Vec::new(), nstored: 0, nfixed: 0, ndynamic: 0 };
    s.inflate();
    let (mut a1, mut a2) = (1u32, 0u32);
    for &b in &s.out {
        a1 = (a1 + b as u32) % 65521;
        a2 = (a2 + a1) % 65521;
    }
    let adl = (a2 << 16) | a1;
    let want = if s.pos + 4 <= idat.len() { be32(&idat, s.pos) } else { !adl };
    if adl != want {
        o.push_str("error: Adler-32 mismatch\n");
        return;
    }
    let out = &s.out;
    let chans: usize = match ctype {
        0 | 3 => 1,
        2 => 3,
        4 => 2,
        _ => 4,
    };
    let bpp_bits = chans * depth as usize;
    let stride = (w * bpp_bits + 7) / 8;
    let bpp = ((bpp_bits + 7) / 8).max(1);
    let mut prow = vec![0u8; stride];
    let mut cur = vec![0u8; stride];
    let mut fc = [0u64; 5];
    let mut sum = [0u64; 4];
    let mut crc = 0xffffffffu32;
    let mut rgba = vec![0u8; w * 4];
    let mut ip = 0usize;
    let maxv: u32 = if depth >= 16 { 0xffff } else { (1u32 << depth) - 1 };
    let cv = |v: u32| -> u8 {
        if depth == 16 {
            (v >> 8) as u8
        } else if depth == 8 {
            v as u8
        } else {
            (v * 255 / maxv) as u8
        }
    };
    let tv = |i: usize| -> u32 { ((trns[2 * i] as u32) << 8) | trns[2 * i + 1] as u32 };
    for _y in 0..h {
        let ft = if ip < out.len() { out[ip] } else { 0 };
        ip += 1;
        if (ft as usize) < 5 {
            fc[ft as usize] += 1;
        }
        for i in 0..stride {
            cur[i] = if ip + i < out.len() { out[ip + i] } else { 0 };
        }
        ip += stride;
        for i in 0..stride {
            let a = if i >= bpp { cur[i - bpp] as i32 } else { 0 };
            let b = prow[i] as i32;
            let c = if i >= bpp { prow[i - bpp] as i32 } else { 0 };
            let x = cur[i] as i32;
            let r = match ft {
                1 => x + a,
                2 => x + b,
                3 => x + (a + b) / 2,
                4 => x + paeth(a, b, c),
                _ => x,
            };
            cur[i] = r as u8;
        }
        for x in 0..w {
            let mut v = [0u32; 4];
            for ch in 0..chans {
                v[ch] = if depth == 16 {
                    let o2 = (x * chans + ch) * 2;
                    ((cur[o2] as u32) << 8) | cur[o2 + 1] as u32
                } else if depth == 8 {
                    cur[x * chans + ch] as u32
                } else {
                    let bit = (x * chans + ch) * depth as usize;
                    ((cur[bit / 8] as u32) >> (8 - depth as usize - bit % 8)) & maxv
                };
            }
            let (r, g, b, a);
            match ctype {
                3 => {
                    let i = v[0] as usize;
                    r = *pal.get(i * 3).unwrap_or(&0);
                    g = *pal.get(i * 3 + 1).unwrap_or(&0);
                    b = *pal.get(i * 3 + 2).unwrap_or(&0);
                    a = *trns.get(i).unwrap_or(&255);
                }
                0 => {
                    r = cv(v[0]);
                    g = r;
                    b = r;
                    a = if hastrns && trns.len() >= 2 && v[0] == tv(0) { 0 } else { 255 };
                }
                2 => {
                    r = cv(v[0]);
                    g = cv(v[1]);
                    b = cv(v[2]);
                    a = if hastrns && trns.len() >= 6 && v[0] == tv(0) && v[1] == tv(1) && v[2] == tv(2) { 0 } else { 255 };
                }
                4 => {
                    r = cv(v[0]);
                    g = r;
                    b = r;
                    a = cv(v[1]);
                }
                _ => {
                    r = cv(v[0]);
                    g = cv(v[1]);
                    b = cv(v[2]);
                    a = cv(v[3]);
                }
            }
            let q = &mut rgba[x * 4..x * 4 + 4];
            q[0] = r;
            q[1] = g;
            q[2] = b;
            q[3] = a;
            sum[0] += r as u64;
            sum[1] += g as u64;
            sum[2] += b as u64;
            sum[3] += a as u64;
        }
        crc = crc_update(t, crc, &rgba);
        std::mem::swap(&mut prow, &mut cur);
    }
    o.push_str(&format!("IHDR {}x{} depth={} color={}\n", w, h, depth, ctype));
    o.push_str(&format!("chunks {}\n", chunkline.join(" ")));
    o.push_str(&format!("deflate stored={} fixed={} dynamic={}\n", s.nstored, s.nfixed, s.ndynamic));
    o.push_str(&format!(
        "filters none={} sub={} up={} average={} paeth={}\n",
        fc[0], fc[1], fc[2], fc[3], fc[4]
    ));
    o.push_str(&format!(
        "rgba sum={},{},{},{} crc32={:08x}\n",
        sum[0], sum[1], sum[2], sum[3], crc ^ 0xffffffff
    ));
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        return;
    }
    let t = crc_table();
    let mut names: Vec<Vec<u8>> = Vec::new();
    if let Ok(rd) = fs::read_dir(&args[1]) {
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().to_string();
            if n.ends_with(".png") {
                names.push(n.into_bytes());
            }
        }
    }
    names.sort();
    let mut o = String::new();
    for n in names {
        process(&t, &args[1], &String::from_utf8(n).unwrap(), &mut o);
    }
    std::io::stdout().write_all(o.as_bytes()).unwrap();
}
