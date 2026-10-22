use std::fs;

// Ordered (insertion-order) counter to mirror Python dict + max() first-max tie-break.
struct OrderedCounter {
    keys: Vec<String>,
    counts: Vec<i64>,
}

impl OrderedCounter {
    fn new() -> Self {
        OrderedCounter { keys: Vec::new(), counts: Vec::new() }
    }

    fn inc(&mut self, key: &str) {
        for i in 0..self.keys.len() {
            if self.keys[i] == key {
                self.counts[i] += 1;
                return;
            }
        }
        self.keys.push(key.to_string());
        self.counts.push(1);
    }

    // First key with the maximum count (Python max() semantics).
    fn argmax(&self) -> usize {
        let mut best = 0usize;
        for i in 1..self.counts.len() {
            if self.counts[i] > self.counts[best] {
                best = i;
            }
        }
        best
    }
}

fn main() {
    let path = "__FIXTURE_DIR__/app.log";
    let content = fs::read_to_string(path).expect("failed to read log file");

    let mut levels = OrderedCounter::new();
    let mut err_comp = OrderedCounter::new();
    let mut hours = OrderedCounter::new();
    let mut err_msgs: Vec<String> = Vec::new();

    for raw in content.lines() {
        let line = raw.trim_end_matches(['\r', '\n']);
        if line.is_empty() || !line.starts_with('[') {
            continue;
        }
        // Format: [YYYY-MM-DD HH:MM:SS] LEVEL (component) - message
        let rb = match line.find(']') {
            Some(i) => i,
            None => continue,
        };

        // timestamp "[YYYY-MM-DD HH:MM:SS]" -> HH at byte offset 12..14
        let bytes = line.as_bytes();
        if bytes.len() < 14 {
            continue;
        }
        let hour = &line[12..14];

        // after "] "
        let mut rest = &line[rb + 1..];
        rest = rest.trim_start_matches(' ');

        // LEVEL = up to next space
        let sp = match rest.find(' ') {
            Some(i) => i,
            None => continue,
        };
        let level = &rest[..sp];
        let after_level = &rest[sp..];

        // component within parentheses
        let lp = match after_level.find('(') {
            Some(i) => i,
            None => continue,
        };
        let rp = match after_level[lp..].find(')') {
            Some(i) => lp + i,
            None => continue,
        };
        let comp = &after_level[lp + 1..rp];

        // message after first " - " following the component
        let tail = &after_level[rp..];
        let msg = tail.find(" - ").map(|i| &tail[i + 3..]);

        levels.inc(level);
        hours.inc(hour);

        if level == "ERROR" {
            err_comp.inc(comp);
            if let Some(m) = msg {
                if !err_msgs.iter().any(|e| e == m) {
                    err_msgs.push(m.to_string());
                }
            }
        }
    }

    let mut out = String::new();
    out.push_str("=== Log Analysis ===\n\n");

    out.push_str("Entries by Level:\n");
    let mut lvl_order: Vec<usize> = (0..levels.keys.len()).collect();
    lvl_order.sort_by(|&a, &b| levels.keys[a].cmp(&levels.keys[b]));
    for &i in &lvl_order {
        out.push_str(&format!("  {}: {}\n", levels.keys[i], levels.counts[i]));
    }

    let ce = err_comp.argmax();
    out.push_str(&format!(
        "\nComponent with Most Errors: {} ({} errors)\n",
        err_comp.keys[ce], err_comp.counts[ce]
    ));

    out.push_str("\nUnique Error Messages:\n");
    err_msgs.sort();
    for m in &err_msgs {
        out.push_str(&format!("  - {}\n", m));
    }

    let bh = hours.argmax();
    out.push_str(&format!(
        "\nBusiest Hour: {}:00 ({} entries)",
        hours.keys[bh], hours.counts[bh]
    ));

    print!("{}", out);
}
