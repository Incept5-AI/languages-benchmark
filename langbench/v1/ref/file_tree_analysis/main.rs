use std::fs;
use std::path::Path;

struct FileInfo {
    rel: String,
    ext: String,
    lines: i64,
    chars: i64,
}

fn measure(path: &Path) -> (i64, i64) {
    let data = fs::read(path).unwrap_or_default();
    let bytes = data.len() as i64;
    let mut lines: i64 = 0;
    for &b in &data {
        if b == b'\n' {
            lines += 1;
        }
    }
    // splitlines semantics: a final segment without a trailing newline counts.
    if !data.is_empty() && *data.last().unwrap() != b'\n' {
        lines += 1;
    }
    (lines, bytes)
}

fn walk(dir: &Path, rel_prefix: &str, out: &mut Vec<FileInfo>) {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let path = entry.path();
        let rel = if rel_prefix.is_empty() {
            name.clone()
        } else {
            format!("{}/{}", rel_prefix, name)
        };
        let meta = match entry.metadata() {
            Ok(m) => m,
            Err(_) => continue,
        };
        if meta.is_dir() {
            walk(&path, &rel, out);
        } else if meta.is_file() {
            let ext = match name.rfind('.') {
                Some(i) if i > 0 => name[i..].to_string(),
                _ => String::new(),
            };
            let (lines, chars) = measure(&path);
            out.push(FileInfo { rel, ext, lines, chars });
        }
    }
}

fn main() {
    let root = Path::new("__FIXTURE_DIR__/project_tree");
    let mut files: Vec<FileInfo> = Vec::new();
    walk(root, "", &mut files);

    files.sort_by(|a, b| a.ext.cmp(&b.ext).then(a.rel.cmp(&b.rel)));

    let mut out = String::new();
    out.push_str("=== File Tree Analysis ===\n\n");

    let mut grand_files: i64 = 0;
    let mut grand_lines: i64 = 0;
    let mut grand_chars: i64 = 0;

    let mut i = 0;
    while i < files.len() {
        let ext = &files[i].ext;
        let mut j = i;
        let (mut ef, mut el, mut ec) = (0i64, 0i64, 0i64);
        while j < files.len() && &files[j].ext == ext {
            ef += 1;
            el += files[j].lines;
            ec += files[j].chars;
            j += 1;
        }
        out.push_str(&format!("Extension: {}\n", ext));
        out.push_str(&format!("  Files: {}\n", ef));
        out.push_str(&format!("  Total Lines: {}\n", el));
        out.push_str(&format!("  Total Characters: {}\n", ec));
        for f in &files[i..j] {
            out.push_str(&format!("    {}: {} lines, {} chars\n", f.rel, f.lines, f.chars));
        }
        grand_files += ef;
        grand_lines += el;
        grand_chars += ec;
        i = j;
    }

    out.push_str(&format!(
        "\nTotal: {} files, {} lines, {} characters\n",
        grand_files, grand_lines, grand_chars
    ));

    print!("{}", out);
}
