use std::collections::HashMap;
use std::fs;
use std::io::Write;

enum J {
    Null,
    Bool(bool),
    Num(i64),
    Str(String),
    Arr(Vec<J>),
    Obj(HashMap<String, J>),
}

struct Parser<'a> {
    b: &'a [u8],
    p: usize,
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        while self.p < self.b.len() && matches!(self.b[self.p], b' ' | b'\t' | b'\n' | b'\r') {
            self.p += 1;
        }
    }
    fn s(&mut self) -> String {
        self.p += 1;
        let mut out = Vec::new();
        while self.p < self.b.len() && self.b[self.p] != b'"' {
            let c = self.b[self.p];
            self.p += 1;
            if c == b'\\' {
                let e = self.b[self.p];
                self.p += 1;
                match e {
                    b'n' => out.push(b'\n'),
                    b't' => out.push(b'\t'),
                    b'r' => out.push(b'\r'),
                    b'b' => out.push(8),
                    b'f' => out.push(12),
                    b'u' => {
                        self.p += 4;
                        out.push(b'?');
                    }
                    _ => out.push(e),
                }
            } else {
                out.push(c);
            }
        }
        self.p += 1;
        String::from_utf8_lossy(&out).into_owned()
    }
    fn v(&mut self) -> J {
        self.ws();
        let c = self.b[self.p];
        match c {
            b'{' => {
                self.p += 1;
                let mut m = HashMap::new();
                self.ws();
                if self.b[self.p] == b'}' {
                    self.p += 1;
                    return J::Obj(m);
                }
                loop {
                    self.ws();
                    let k = self.s();
                    self.ws();
                    self.p += 1; // ':'
                    let v = self.v();
                    m.insert(k, v);
                    self.ws();
                    let c = self.b[self.p];
                    self.p += 1;
                    if c == b'}' {
                        break;
                    }
                }
                J::Obj(m)
            }
            b'[' => {
                self.p += 1;
                let mut a = Vec::new();
                self.ws();
                if self.b[self.p] == b']' {
                    self.p += 1;
                    return J::Arr(a);
                }
                loop {
                    a.push(self.v());
                    self.ws();
                    let c = self.b[self.p];
                    self.p += 1;
                    if c == b']' {
                        break;
                    }
                }
                J::Arr(a)
            }
            b'"' => J::Str(self.s()),
            b't' => {
                self.p += 4;
                J::Bool(true)
            }
            b'f' => {
                self.p += 5;
                J::Bool(false)
            }
            b'n' => {
                self.p += 4;
                J::Null
            }
            _ => {
                let st = self.p;
                while self.p < self.b.len()
                    && matches!(self.b[self.p], b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9')
                {
                    self.p += 1;
                }
                let t = std::str::from_utf8(&self.b[st..self.p]).unwrap();
                let n = t.parse::<i64>().unwrap_or_else(|_| t.parse::<f64>().unwrap() as i64);
                J::Num(n)
            }
        }
    }
}

impl J {
    fn get(&self, k: &str) -> &J {
        match self {
            J::Obj(m) => m.get(k).unwrap_or(&J::Null),
            _ => &J::Null,
        }
    }
    fn n(&self) -> i64 {
        match self {
            J::Num(n) => *n,
            _ => 0,
        }
    }
    fn arr(&self) -> &[J] {
        match self {
            J::Arr(a) => a,
            _ => &[],
        }
    }
    fn st(&self) -> &str {
        match self {
            J::Str(s) => s,
            _ => "",
        }
    }
}

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

fn chunk(out: &mut Vec<u8>, t: &[u32; 256], typ: &[u8], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    let mut c = 0xffffffffu32;
    for &b in typ.iter().chain(data.iter()) {
        c = t[((c ^ b as u32) & 0xff) as usize] ^ (c >> 8);
    }
    out.extend_from_slice(typ);
    out.extend_from_slice(data);
    out.extend_from_slice(&(c ^ 0xffffffff).to_be_bytes());
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let text = fs::read(format!("{}/scene.json", args[1])).unwrap();
    let root = Parser { b: &text, p: 0 }.v();
    let w = root.get("width").n();
    let h = root.get("height").n();
    let bg: Vec<u8> = root.get("background").arr().iter().map(|v| v.n() as u8).collect();
    let mut img = vec![0u8; (w * h * 3) as usize];
    for i in 0..(w * h) as usize {
        img[i * 3..i * 3 + 3].copy_from_slice(&bg[0..3]);
    }
    let mut out = String::new();
    for (si, s) in root.get("shapes").arr().iter().enumerate() {
        let typ = s.get("type").st().to_string();
        let col: Vec<i64> = s.get("color").arr().iter().map(|v| v.n()).collect();
        let a = col[3];
        let (mut x0, mut y0, mut x1, mut y1);
        let (mut rx, mut ry, mut cx, mut cy, mut r) = (0i64, 0i64, 0i64, 0i64, 0i64);
        let mut px = [0i64; 3];
        let mut py = [0i64; 3];
        let (mut lx1, mut ly1, mut lx2, mut ly2) = (0i64, 0i64, 0i64, 0i64);
        let kind;
        match typ.as_str() {
            "rect" => {
                kind = 0;
                rx = s.get("x").n();
                ry = s.get("y").n();
                x0 = rx;
                y0 = ry;
                x1 = rx + s.get("w").n() - 1;
                y1 = ry + s.get("h").n() - 1;
            }
            "circle" => {
                kind = 1;
                cx = s.get("cx").n();
                cy = s.get("cy").n();
                r = s.get("r").n();
                x0 = cx - r;
                x1 = cx + r;
                y0 = cy - r;
                y1 = cy + r;
            }
            "triangle" => {
                kind = 2;
                let pts = s.get("points").arr();
                for i in 0..3 {
                    px[i] = pts[i].arr()[0].n();
                    py[i] = pts[i].arr()[1].n();
                }
                x0 = *px.iter().min().unwrap();
                x1 = *px.iter().max().unwrap();
                y0 = *py.iter().min().unwrap();
                y1 = *py.iter().max().unwrap();
            }
            _ => {
                kind = 3;
                lx1 = s.get("x1").n();
                ly1 = s.get("y1").n();
                lx2 = s.get("x2").n();
                ly2 = s.get("y2").n();
                r = s.get("r").n();
                x0 = lx1.min(lx2) - r;
                x1 = lx1.max(lx2) + r;
                y0 = ly1.min(ly2) - r;
                y1 = ly1.max(ly2) + r;
            }
        }
        let _ = (rx, ry);
        x0 = x0.max(0);
        y0 = y0.max(0);
        x1 = x1.min(w - 1);
        y1 = y1.min(h - 1);
        let r2 = r * r;
        let (dx, dy) = (lx2 - lx1, ly2 - ly1);
        let l = dx * dx + dy * dy;
        let mut count: i64 = 0;
        let mut y = y0;
        while y <= y1 {
            let mut x = x0;
            while x <= x1 {
                let cov = match kind {
                    0 => true,
                    1 => (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r2,
                    2 => {
                        let mut pos = true;
                        let mut neg = true;
                        for i in 0..3 {
                            let j = (i + 1) % 3;
                            let e = (px[j] - px[i]) * (y - py[i]) - (py[j] - py[i]) * (x - px[i]);
                            if e < 0 {
                                pos = false;
                            }
                            if e > 0 {
                                neg = false;
                            }
                        }
                        pos || neg
                    }
                    _ => {
                        let t = (x - lx1) * dx + (y - ly1) * dy;
                        if t <= 0 {
                            (x - lx1) * (x - lx1) + (y - ly1) * (y - ly1) <= r2
                        } else if t >= l {
                            (x - lx2) * (x - lx2) + (y - ly2) * (y - ly2) <= r2
                        } else {
                            let c = (x - lx1) * dy - (y - ly1) * dx;
                            c * c <= r2 * l
                        }
                    }
                };
                if cov {
                    count += 1;
                    let o = ((y * w + x) * 3) as usize;
                    for c in 0..3 {
                        let d = img[o + c] as i64;
                        img[o + c] = ((col[c] * a + d * (255 - a) + 127) / 255) as u8;
                    }
                }
                x += 1;
            }
            y += 1;
        }
        out.push_str(&format!("{} {} {}\n", si + 1, typ, count));
    }
    let mut seen = vec![0u8; 1 << 21];
    let mut k = 0;
    for i in 0..(w * h) as usize {
        let v = ((img[i * 3] as usize) << 16) | ((img[i * 3 + 1] as usize) << 8) | img[i * 3 + 2] as usize;
        if seen[v >> 3] & (1 << (v & 7)) == 0 {
            seen[v >> 3] |= 1 << (v & 7);
            k += 1;
        }
    }
    out.push_str(&format!("colors {}\n", k));

    let stride = (w * 3) as usize;
    let mut raw = Vec::with_capacity((h as usize) * (stride + 1));
    for y in 0..h as usize {
        raw.push(0u8);
        raw.extend_from_slice(&img[y * stride..(y + 1) * stride]);
    }
    let mut z = vec![0x78u8, 0x01];
    let chunks: Vec<&[u8]> = raw.chunks(65535).collect();
    for (i, c) in chunks.iter().enumerate() {
        z.push(if i == chunks.len() - 1 { 1 } else { 0 });
        let n = c.len() as u16;
        z.extend_from_slice(&n.to_le_bytes());
        z.extend_from_slice(&(!n).to_le_bytes());
        z.extend_from_slice(c);
    }
    let (mut aa, mut bb) = (1u32, 0u32);
    for &b in &raw {
        aa = (aa + b as u32) % 65521;
        bb = (bb + aa) % 65521;
    }
    z.extend_from_slice(&((bb << 16) | aa).to_be_bytes());
    let t = crc_table();
    let mut png = vec![137u8, 80, 78, 71, 13, 10, 26, 10];
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&(w as u32).to_be_bytes());
    ihdr.extend_from_slice(&(h as u32).to_be_bytes());
    ihdr.extend_from_slice(&[8, 2, 0, 0, 0]);
    chunk(&mut png, &t, b"IHDR", &ihdr);
    chunk(&mut png, &t, b"IDAT", &z);
    chunk(&mut png, &t, b"IEND", &[]);
    fs::write("out.png", &png).unwrap();
    let so = std::io::stdout();
    let mut lock = so.lock();
    lock.write_all(out.as_bytes()).unwrap();
}
