use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::io::{Read, Write};
use std::net::{TcpStream, UdpSocket};

fn rand16(seq: u64) -> u16 {
    let mut h = RandomState::new().build_hasher();
    h.write_u64(seq);
    h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
    (h.finish() & 0xffff) as u16
}

fn read_name(msg: &[u8], mut pos: usize) -> (String, usize) {
    let mut labels: Vec<String> = Vec::new();
    let mut end: Option<usize> = None;
    let mut jumps = 0;
    loop {
        if pos >= msg.len() {
            break;
        }
        let l = msg[pos] as usize;
        if l & 0xC0 == 0xC0 {
            if pos + 1 >= msg.len() {
                break;
            }
            let target = ((l & 0x3F) << 8) | msg[pos + 1] as usize;
            if end.is_none() {
                end = Some(pos + 2);
            }
            jumps += 1;
            if jumps > 255 {
                break;
            }
            pos = target;
            continue;
        }
        if l == 0 {
            if end.is_none() {
                end = Some(pos + 1);
            }
            break;
        }
        let s = pos + 1;
        let e = (s + l).min(msg.len());
        let lab: String = msg[s..e].iter().map(|&b| (b as char).to_ascii_lowercase()).collect();
        labels.push(lab);
        pos = s + l;
    }
    let name = if labels.is_empty() { ".".to_string() } else { labels.join(".") };
    (name, end.unwrap_or(msg.len()))
}

fn u16at(m: &[u8], p: usize) -> usize {
    ((m[p] as usize) << 8) | m[p + 1] as usize
}

fn type_name(t: usize) -> String {
    match t {
        1 => "A".into(),
        5 => "CNAME".into(),
        15 => "MX".into(),
        16 => "TXT".into(),
        28 => "AAAA".into(),
        _ => format!("TYPE{}", t),
    }
}

fn aaaa(d: &[u8]) -> String {
    let g: Vec<u16> = (0..8).map(|i| ((d[2 * i] as u16) << 8) | d[2 * i + 1] as u16).collect();
    let (mut bs, mut bl) = (usize::MAX, 0usize);
    let mut i = 0;
    while i < 8 {
        if g[i] == 0 {
            let mut j = i;
            while j < 8 && g[j] == 0 {
                j += 1;
            }
            if j - i > bl {
                bl = j - i;
                bs = i;
            }
            i = j;
        } else {
            i += 1;
        }
    }
    if bl < 2 {
        return g.iter().map(|x| format!("{:x}", x)).collect::<Vec<_>>().join(":");
    }
    let left = g[..bs].iter().map(|x| format!("{:x}", x)).collect::<Vec<_>>().join(":");
    let right = g[bs + bl..].iter().map(|x| format!("{:x}", x)).collect::<Vec<_>>().join(":");
    format!("{}::{}", left, right)
}

fn txt(d: &[u8]) -> String {
    let mut parts = Vec::new();
    let mut p = 0;
    while p < d.len() {
        let l = d[p] as usize;
        p += 1;
        let e = (p + l).min(d.len());
        let mut s = String::from("\"");
        for &b in &d[p..e] {
            match b {
                b'"' => s.push_str("\\\""),
                b'\\' => s.push_str("\\\\"),
                0x20..=0x7e => s.push(b as char),
                _ => s.push_str(&format!("\\{:03}", b)),
            }
        }
        s.push('"');
        parts.push(s);
        p = e;
    }
    parts.join(" ")
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dir = &args[1];
    let port: u16 = args[2].trim().parse().unwrap();
    let content = std::fs::read_to_string(format!("{}/queries.txt", dir)).unwrap_or_default();
    let sock = UdpSocket::bind("127.0.0.1:0").unwrap();
    let addr = format!("127.0.0.1:{}", port);
    let mut out = String::new();
    let mut seq = 0u64;
    for line in content.lines() {
        let line = line.trim_end_matches('\r');
        if line.trim().is_empty() {
            continue;
        }
        let mut it = line.trim().split_whitespace();
        let name = it.next().unwrap_or("");
        let tname = it.next().unwrap_or("A");
        let qtype: u16 = match tname {
            "A" => 1,
            "CNAME" => 5,
            "MX" => 15,
            "TXT" => 16,
            "AAAA" => 28,
            _ => 1,
        };
        seq += 1;
        let id = rand16(seq);
        let mut q: Vec<u8> = Vec::new();
        q.extend_from_slice(&id.to_be_bytes());
        q.extend_from_slice(&[0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0]);
        let trimmed = name.strip_suffix('.').unwrap_or(name);
        if !trimmed.is_empty() {
            for lab in trimmed.split('.') {
                q.push(lab.len() as u8);
                q.extend_from_slice(lab.as_bytes());
            }
        }
        q.push(0);
        q.extend_from_slice(&qtype.to_be_bytes());
        q.extend_from_slice(&[0, 1]);

        sock.send_to(&q, &addr).unwrap();
        let mut buf = vec![0u8; 65536];
        let mut msg: Vec<u8>;
        loop {
            let (n, _) = sock.recv_from(&mut buf).unwrap();
            if n < 12 {
                continue;
            }
            let rid = ((buf[0] as u16) << 8) | buf[1] as u16;
            if rid == id && buf[2] & 0x80 != 0 {
                msg = buf[..n].to_vec();
                break;
            }
        }
        if msg[2] & 0x02 != 0 {
            let mut s = TcpStream::connect(&addr).unwrap();
            let mut framed = (q.len() as u16).to_be_bytes().to_vec();
            framed.extend_from_slice(&q);
            s.write_all(&framed).unwrap();
            let mut lb = [0u8; 2];
            s.read_exact(&mut lb).unwrap();
            let len = u16::from_be_bytes(lb) as usize;
            let mut m = vec![0u8; len];
            s.read_exact(&mut m).unwrap();
            msg = m;
        }
        let rcode = msg[3] & 0x0f;
        let rname = match rcode {
            0 => "NOERROR".to_string(),
            1 => "FORMERR".to_string(),
            2 => "SERVFAIL".to_string(),
            3 => "NXDOMAIN".to_string(),
            4 => "NOTIMP".to_string(),
            5 => "REFUSED".to_string(),
            n => format!("RCODE{}", n),
        };
        let lname = trimmed.to_ascii_lowercase();
        let disp = if lname.is_empty() { ".".to_string() } else { lname };
        out.push_str(&format!("{} {} {}\n", disp, tname, rname));
        let qd = u16at(&msg, 4);
        let an = u16at(&msg, 6);
        let mut pos = 12;
        for _ in 0..qd {
            let (_, p) = read_name(&msg, pos);
            pos = p + 4;
        }
        for _ in 0..an {
            let (owner, p) = read_name(&msg, pos);
            pos = p;
            let t = u16at(&msg, pos);
            let ttl = u32::from_be_bytes([msg[pos + 4], msg[pos + 5], msg[pos + 6], msg[pos + 7]]);
            let rdl = u16at(&msg, pos + 8);
            let rs = pos + 10;
            let rd = &msg[rs..rs + rdl];
            let data = match t {
                1 if rdl == 4 => format!("{}.{}.{}.{}", rd[0], rd[1], rd[2], rd[3]),
                28 if rdl == 16 => aaaa(rd),
                5 => read_name(&msg, rs).0,
                15 => format!("{} {}", u16at(&msg, rs), read_name(&msg, rs + 2).0),
                16 => txt(rd),
                _ => {
                    let mut s = format!("\\# {}", rdl);
                    if rdl > 0 {
                        s.push(' ');
                        for b in rd {
                            s.push_str(&format!("{:02x}", b));
                        }
                    }
                    s
                }
            };
            out.push_str(&format!("  {} {} {} {}\n", owner, ttl, type_name(t), data));
            pos = rs + rdl;
        }
    }
    std::io::stdout().write_all(out.as_bytes()).unwrap();
}
