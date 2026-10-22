use std::fmt::Write as FmtWrite;
use std::io::Write;

fn u32le(d: &[u8], o: usize) -> u32 {
    u32::from_le_bytes([d[o], d[o + 1], d[o + 2], d[o + 3]])
}
fn u16le(d: &[u8], o: usize) -> u16 {
    u16::from_le_bytes([d[o], d[o + 1]])
}

fn chan(px: u32, mask: u32) -> i64 {
    if mask == 0 {
        return 0;
    }
    let s = mask.trailing_zeros();
    let n = (mask >> s).trailing_ones();
    let v = ((px & mask) >> s) as u64;
    let m: u64 = (1u64 << n) - 1;
    ((v * 255 + m / 2) / m) as i64
}

fn is_ink(r: i64, g: i64, b: i64) -> bool {
    (299 * r + 587 * g + 114 * b) / 1000 < 128
}

fn process(dir: &str, name: &str, out: &mut String) {
    writeln!(out, "== {}", name).unwrap();
    let d = match std::fs::read(format!("{}/{}", dir, name)) {
        Ok(d) => d,
        Err(_) => return,
    };
    let off = u32le(&d, 10) as usize;
    let hsz = u32le(&d, 14) as usize;
    let w = u32le(&d, 18) as i32 as i64;
    let hraw = u32le(&d, 22) as i32 as i64;
    let bpp = u16le(&d, 28) as usize;
    let comp = u32le(&d, 30);
    let clr_used = u32le(&d, 46) as usize;
    if comp != 0 && comp != 3 {
        writeln!(out, "error: unsupported compression {}", comp).unwrap();
        return;
    }
    let bottom_up = hraw > 0;
    let h = hraw.abs() as usize;
    let w = w as usize;
    let (mut rm, mut gm, mut bm) = (0u32, 0u32, 0u32);
    if comp == 3 {
        let p = if hsz >= 108 { 54 } else { 14 + hsz };
        rm = u32le(&d, p);
        gm = u32le(&d, p + 4);
        bm = u32le(&d, p + 8);
    }
    let pal = 14 + hsz;
    let mut pal_ink: Vec<bool> = Vec::new();
    if bpp <= 8 {
        let n = if clr_used != 0 { clr_used } else { 1 << bpp };
        for i in 0..n {
            let e = pal + 4 * i;
            pal_ink.push(is_ink(d[e + 2] as i64, d[e + 1] as i64, d[e] as i64));
        }
    }
    let stride = (w * bpp + 31) / 32 * 4;
    let mut ink = vec![0u8; w * h];
    let mut cnt_ink = 0usize;
    for y in 0..h {
        let srow = if bottom_up { h - 1 - y } else { y };
        let row = off + srow * stride;
        for x in 0..w {
            let is = if bpp <= 8 {
                let idx = match bpp {
                    8 => d[row + x] as usize,
                    4 => ((d[row + x / 2] >> (if x & 1 == 1 { 0 } else { 4 })) & 15) as usize,
                    _ => ((d[row + x / 8] >> (7 - (x & 7))) & 1) as usize,
                };
                idx < pal_ink.len() && pal_ink[idx]
            } else if bpp == 24 {
                let p = row + 3 * x;
                is_ink(d[p + 2] as i64, d[p + 1] as i64, d[p] as i64)
            } else if comp == 0 {
                let p = row + 4 * x;
                is_ink(d[p + 2] as i64, d[p + 1] as i64, d[p] as i64)
            } else {
                let px = u32le(&d, row + 4 * x);
                is_ink(chan(px, rm), chan(px, gm), chan(px, bm))
            };
            if is {
                ink[y * w + x] = 1;
                cnt_ink += 1;
            }
        }
    }
    writeln!(
        out,
        "size {}x{} bpp {} header {} {} ink {}",
        w,
        h,
        bpp,
        hsz,
        if bottom_up { "bottom-up" } else { "top-down" },
        cnt_ink
    )
    .unwrap();
    let mut lines = String::new();
    let mut ncomp = 0;
    let mut stack: Vec<usize> = Vec::new();
    for y in 0..h {
        for x in 0..w {
            let i0 = y * w + x;
            if ink[i0] != 1 {
                continue;
            }
            ink[i0] = 2;
            stack.push(i0);
            let (mut minx, mut maxx, mut miny, mut maxy, mut cnt) = (x, x, y, y, 0usize);
            while let Some(i) = stack.pop() {
                let cy = i / w;
                let cx = i % w;
                cnt += 1;
                minx = minx.min(cx);
                maxx = maxx.max(cx);
                miny = miny.min(cy);
                maxy = maxy.max(cy);
                for dy in -1i64..=1 {
                    for dx in -1i64..=1 {
                        let nx = cx as i64 + dx;
                        let ny = cy as i64 + dy;
                        if nx < 0 || ny < 0 || nx >= w as i64 || ny >= h as i64 {
                            continue;
                        }
                        let j = ny as usize * w + nx as usize;
                        if ink[j] == 1 {
                            ink[j] = 2;
                            stack.push(j);
                        }
                    }
                }
            }
            ncomp += 1;
            writeln!(lines, "{},{} bbox {},{}-{},{} pixels {}", x, y, minx, miny, maxx, maxy, cnt).unwrap();
        }
    }
    writeln!(out, "components {}", ncomp).unwrap();
    out.push_str(&lines);
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        return;
    }
    let dir = &args[1];
    let mut names: Vec<Vec<u8>> = Vec::new();
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if n.ends_with(".bmp") {
                names.push(n.into_bytes());
            }
        }
    }
    names.sort();
    let mut out = String::new();
    for n in &names {
        process(dir, &String::from_utf8_lossy(n), &mut out);
    }
    std::io::stdout().write_all(out.as_bytes()).unwrap();
}
