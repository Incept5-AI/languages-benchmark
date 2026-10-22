use std::io::{Read, Write};
use std::net::TcpStream;

struct Product {
    name: String,
    price: f64,
}

/// Parse a JSON string starting at the opening quote (bytes[i] == '"').
/// Returns (decoded string, index just past closing quote).
fn parse_string(bytes: &[u8], mut i: usize) -> (String, usize) {
    let mut out = String::new();
    // skip opening quote
    i += 1;
    while i < bytes.len() && bytes[i] != b'"' {
        if bytes[i] == b'\\' && i + 1 < bytes.len() {
            i += 1;
            let c = match bytes[i] {
                b'n' => '\n',
                b't' => '\t',
                b'r' => '\r',
                b'"' => '"',
                b'\\' => '\\',
                b'/' => '/',
                other => other as char,
            };
            out.push(c);
        } else {
            out.push(bytes[i] as char);
        }
        i += 1;
    }
    if i < bytes.len() && bytes[i] == b'"' {
        i += 1; // skip closing quote
    }
    (out, i)
}

fn skip_ws(bytes: &[u8], mut i: usize) -> usize {
    while i < bytes.len() && matches!(bytes[i], b' ' | b'\t' | b'\n' | b'\r') {
        i += 1;
    }
    i
}

fn parse_number(bytes: &[u8], mut i: usize) -> (f64, usize) {
    let start = i;
    while i < bytes.len()
        && matches!(bytes[i], b'0'..=b'9' | b'-' | b'+' | b'.' | b'e' | b'E')
    {
        i += 1;
    }
    let s = std::str::from_utf8(&bytes[start..i]).unwrap_or("0");
    (s.parse::<f64>().unwrap_or(0.0), i)
}

fn main() {
    let addr = "127.0.0.1:__PORT__";
    let mut stream = TcpStream::connect(addr).expect("connect failed");

    let req = "GET /api/products HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    stream.write_all(req.as_bytes()).expect("write failed");

    let mut response = Vec::new();
    stream.read_to_end(&mut response).expect("read failed");

    // Split off HTTP headers at \r\n\r\n.
    let body_start = response
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map(|p| p + 4)
        .unwrap_or(0);
    let body = &response[body_start..];

    // Parse JSON array of objects, extracting "name" and "price" per object.
    let mut products: Vec<Product> = Vec::new();
    let mut i = 0;
    while i < body.len() {
        if body[i] == b'{' {
            i += 1;
            let mut name: Option<String> = None;
            let mut price: Option<f64> = None;
            loop {
                i = skip_ws(body, i);
                if i >= body.len() || body[i] == b'}' {
                    if i < body.len() {
                        i += 1;
                    }
                    break;
                }
                if body[i] != b'"' {
                    i += 1;
                    continue;
                }
                let (key, ni) = parse_string(body, i);
                i = ni;
                i = skip_ws(body, i);
                if i < body.len() && body[i] == b':' {
                    i += 1;
                }
                i = skip_ws(body, i);
                if i >= body.len() {
                    break;
                }
                if body[i] == b'"' {
                    let (val, ni) = parse_string(body, i);
                    i = ni;
                    if key == "name" {
                        name = Some(val);
                    }
                } else {
                    let (num, ni) = parse_number(body, i);
                    i = ni;
                    if key == "price" {
                        price = Some(num);
                    }
                }
                i = skip_ws(body, i);
                if i < body.len() && body[i] == b',' {
                    i += 1;
                }
            }
            if let (Some(n), Some(p)) = (name, price) {
                if p > 50.00 {
                    products.push(Product { name: n, price: p });
                }
            }
        } else {
            i += 1;
        }
    }

    products.sort_by(|a, b| a.name.cmp(&b.name));

    let mut out = String::new();
    out.push_str("=== Filtered Products (price > $50.00) ===\n\n");
    let mut total = 0.0;
    for p in &products {
        out.push_str(&format!("{}: ${:.2}\n", p.name, p.price));
        total += p.price;
    }
    out.push_str(&format!("\nCount: {}\n", products.len()));
    out.push_str(&format!("Total: ${:.2}\n", total));

    print!("{}", out);
}
