use std::fs::File;
use std::io::{self, BufReader, Read, Write};
use std::net::TcpStream;

fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0];
    let ml = (data.len() as u64).wrapping_mul(8);
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&ml.to_be_bytes());
    let mut w = [0u32; 80];
    for chunk in msg.chunks(64) {
        for i in 0..16 {
            w[i] = u32::from_be_bytes([chunk[4 * i], chunk[4 * i + 1], chunk[4 * i + 2], chunk[4 * i + 3]]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let (mut a, mut b, mut c, mut d, mut e) = (h[0], h[1], h[2], h[3], h[4]);
        for i in 0..80 {
            let (f, k) = if i < 20 {
                ((b & c) | (!b & d), 0x5A827999)
            } else if i < 40 {
                (b ^ c ^ d, 0x6ED9EBA1)
            } else if i < 60 {
                ((b & c) | (b & d) | (c & d), 0x8F1BBCDC)
            } else {
                (b ^ c ^ d, 0xCA62C1D6u32)
            };
            let t = a.rotate_left(5).wrapping_add(f).wrapping_add(e).wrapping_add(k).wrapping_add(w[i]);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = t;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
    }
    let mut out = [0u8; 20];
    for i in 0..5 {
        out[4 * i..4 * i + 4].copy_from_slice(&h[i].to_be_bytes());
    }
    out
}

fn b64(data: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut s = String::new();
    for ch in data.chunks(3) {
        let b = [ch[0], *ch.get(1).unwrap_or(&0), *ch.get(2).unwrap_or(&0)];
        let v = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        s.push(T[(v >> 18) as usize & 63] as char);
        s.push(T[(v >> 12) as usize & 63] as char);
        s.push(if ch.len() > 1 { T[(v >> 6) as usize & 63] as char } else { '=' });
        s.push(if ch.len() > 2 { T[v as usize & 63] as char } else { '=' });
    }
    s
}

struct Rng(File);
impl Rng {
    fn bytes(&mut self, n: usize) -> Vec<u8> {
        let mut v = vec![0u8; n];
        self.0.read_exact(&mut v).unwrap();
        v
    }
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{:02x}", x)).collect()
}

fn send_frame(s: &mut TcpStream, rng: &mut Rng, op: u8, p: &[u8]) -> io::Result<()> {
    let len = p.len();
    let mut f = Vec::with_capacity(len + 14);
    f.push(0x80 | op);
    if len <= 125 {
        f.push(0x80 | len as u8);
    } else if len <= 65535 {
        f.push(0x80 | 126);
        f.extend_from_slice(&(len as u16).to_be_bytes());
    } else {
        f.push(0x80 | 127);
        f.extend_from_slice(&(len as u64).to_be_bytes());
    }
    let mut mk;
    loop {
        mk = rng.bytes(4);
        if mk.iter().any(|&x| x != 0) {
            break;
        }
    }
    f.extend_from_slice(&mk);
    for (i, b) in p.iter().enumerate() {
        f.push(b ^ mk[i & 3]);
    }
    s.write_all(&f)?;
    s.flush()
}

fn read_n<R: Read>(r: &mut R, n: usize) -> io::Result<Vec<u8>> {
    let mut v = vec![0u8; n];
    r.read_exact(&mut v)?;
    Ok(v)
}

fn session(url: &str, rng: &mut Rng, out: &mut impl Write) -> io::Result<()> {
    let rest = url.strip_prefix("ws://").unwrap_or(url);
    let (hostport, target) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let mut stream = TcpStream::connect(hostport)?;
    stream.set_nodelay(true).ok();
    let key = b64(&rng.bytes(16));
    let req = format!(
        "GET {} HTTP/1.1\r\nHost: {}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\n\r\n",
        target, hostport, key
    );
    stream.write_all(req.as_bytes())?;
    let mut ws = stream.try_clone()?;
    let mut r = BufReader::with_capacity(65536, stream);

    let mut head: Vec<u8> = Vec::new();
    let mut one = [0u8; 1];
    loop {
        if r.read(&mut one)? == 0 {
            return Ok(());
        }
        head.push(one[0]);
        if head.ends_with(b"\r\n\r\n") || head.ends_with(b"\n\n") {
            break;
        }
    }
    let head = String::from_utf8_lossy(&head).to_string();
    let mut lines = head.lines();
    let status = lines.next().unwrap_or("");
    let after = status.splitn(2, ' ').nth(1).unwrap_or("");
    let mut parts = after.splitn(2, ' ');
    let code = parts.next().unwrap_or("");
    let reason = parts.next().unwrap_or("");
    if code != "101" {
        writeln!(out, "handshake failed: {} {}", code, reason)?;
        return Ok(());
    }
    let mut accept: Option<String> = None;
    for l in lines {
        if let Some(c) = l.find(':') {
            if l[..c].trim().eq_ignore_ascii_case("Sec-WebSocket-Accept") {
                accept = Some(l[c + 1..].trim().to_string());
            }
        }
    }
    let expect = b64(&sha1(format!("{}258EAFA5-E914-47DA-95CA-C5AB0DC85B11", key).as_bytes()));
    if accept.as_deref() != Some(expect.as_str()) {
        writeln!(out, "handshake failed: bad Sec-WebSocket-Accept")?;
        return Ok(());
    }

    let mut close_sent = false;
    let mut pending_sha = false;
    let mut msg_op = 0u8;
    let mut msg: Vec<u8> = Vec::new();
    loop {
        let h = match read_n(&mut r, 2) {
            Ok(h) => h,
            Err(_) => return Ok(()),
        };
        let fin = h[0] & 0x80 != 0;
        let op = h[0] & 0x0f;
        let mut len = (h[1] & 0x7f) as u64;
        if len == 126 {
            let x = read_n(&mut r, 2)?;
            len = u16::from_be_bytes([x[0], x[1]]) as u64;
        } else if len == 127 {
            let x = read_n(&mut r, 8)?;
            let mut a = [0u8; 8];
            a.copy_from_slice(&x);
            len = u64::from_be_bytes(a);
        }
        let mk = if h[1] & 0x80 != 0 { Some(read_n(&mut r, 4)?) } else { None };
        let mut p = read_n(&mut r, len as usize)?;
        if let Some(m) = mk {
            for i in 0..p.len() {
                p[i] ^= m[i & 3];
            }
        }
        match op {
            9 => {
                send_frame(&mut ws, rng, 10, &p)?;
                continue;
            }
            10 => continue,
            8 => {
                if p.len() >= 2 {
                    let cc = ((p[0] as u16) << 8) | p[1] as u16;
                    let reason = String::from_utf8_lossy(&p[2..]);
                    if reason.is_empty() {
                        writeln!(out, "closed {}", cc)?;
                    } else {
                        writeln!(out, "closed {} {}", cc, reason)?;
                    }
                } else {
                    writeln!(out, "closed 1005")?;
                }
                out.flush()?;
                if !close_sent {
                    let n = if p.len() >= 2 { 2 } else { 0 };
                    let _ = send_frame(&mut ws, rng, 8, &p[..n]);
                }
                return Ok(());
            }
            1 | 2 => {
                msg_op = op;
                msg.clear();
                msg.extend_from_slice(&p);
            }
            0 => msg.extend_from_slice(&p),
            _ => continue,
        }
        if !fin {
            continue;
        }
        let data = std::mem::take(&mut msg);
        if msg_op == 1 {
            let cmd = String::from_utf8_lossy(&data).to_string();
            if cmd.starts_with("ADD ") {
                let v: Vec<&str> = cmd.split_whitespace().collect();
                let a: i64 = v.get(1).and_then(|x| x.parse().ok()).unwrap_or(0);
                let b: i64 = v.get(2).and_then(|x| x.parse().ok()).unwrap_or(0);
                let rep = (a + b).to_string();
                send_frame(&mut ws, rng, 1, rep.as_bytes())?;
                writeln!(out, "{} -> {}", cmd, rep)?;
            } else if cmd.starts_with("REV") {
                let t = match cmd.find(' ') {
                    Some(i) => &cmd[i + 1..],
                    None => "",
                };
                let rep: String = t.chars().rev().collect();
                send_frame(&mut ws, rng, 1, rep.as_bytes())?;
                writeln!(out, "{} -> {}", cmd, rep)?;
            } else if cmd == "SHA1" {
                pending_sha = true;
            } else if cmd == "BYE" {
                send_frame(&mut ws, rng, 8, &[0x03, 0xE8])?;
                close_sent = true;
            }
        } else if msg_op == 2 && pending_sha {
            pending_sha = false;
            let hx = hex(&sha1(&data));
            send_frame(&mut ws, rng, 1, hx.as_bytes())?;
            writeln!(out, "SHA1 {} -> {}", data.len(), hx)?;
        }
        out.flush()?;
    }
}

fn main() {
    let mut rng = Rng(File::open("/dev/urandom").unwrap());
    let stdout = io::stdout();
    let mut out = stdout.lock();
    for (i, url) in std::env::args().skip(1).enumerate() {
        let _ = writeln!(out, "session {}", i + 1);
        let _ = session(&url, &mut rng, &mut out);
        let _ = out.flush();
    }
}
