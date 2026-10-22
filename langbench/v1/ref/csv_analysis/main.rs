use std::fs;

fn comma_int(v: i64) -> String {
    let s = v.to_string();
    let bytes = s.as_bytes();
    let n = bytes.len();
    let mut out = String::new();
    for i in 0..n {
        if i > 0 && (n - i) % 3 == 0 {
            out.push(',');
        }
        out.push(bytes[i] as char);
    }
    out
}

fn comma_avg(v: f64) -> String {
    let cents = (v * 100.0 + 0.5).floor() as i64;
    let ipart = cents / 100;
    let frac = cents % 100;
    format!("{}.{:02}", comma_int(ipart), frac)
}

fn main() {
    let path = "__FIXTURE_DIR__/employees.csv";
    let content = fs::read_to_string(path).expect("read csv");

    let mut names: Vec<String> = Vec::new();
    let mut salaries: Vec<i64> = Vec::new();
    let mut emp_dept: Vec<String> = Vec::new();

    // department order of first appearance, with totals/counts
    let mut dept_names: Vec<String> = Vec::new();
    let mut dept_total: Vec<i64> = Vec::new();
    let mut dept_count: Vec<i32> = Vec::new();

    for (i, line) in content.lines().enumerate() {
        if i == 0 {
            continue;
        }
        if line.is_empty() {
            continue;
        }
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 6 {
            continue;
        }
        let name = cols[1].to_string();
        let dept = cols[2].to_string();
        let salary: i64 = cols[3].parse().unwrap_or(0);

        let idx = match dept_names.iter().position(|d| d == &dept) {
            Some(p) => p,
            None => {
                dept_names.push(dept.clone());
                dept_total.push(0);
                dept_count.push(0);
                dept_names.len() - 1
            }
        };
        dept_total[idx] += salary;
        dept_count[idx] += 1;

        names.push(name);
        salaries.push(salary);
        emp_dept.push(dept);
    }

    // sort department indices alphabetically
    let mut order: Vec<usize> = (0..dept_names.len()).collect();
    order.sort_by(|&a, &b| dept_names[a].cmp(&dept_names[b]));

    let mut out = String::new();
    out.push_str("=== Salary Report ===\n\n");
    out.push_str("Average Salary by Department:\n");
    for &di in &order {
        let avg = dept_total[di] as f64 / dept_count[di] as f64;
        out.push_str(&format!("  {}: ${}\n", dept_names[di], comma_avg(avg)));
    }

    // highest paid: first max (matches python max)
    let mut top = 0usize;
    for i in 1..salaries.len() {
        if salaries[i] > salaries[top] {
            top = i;
        }
    }
    out.push_str(&format!(
        "\nHighest Paid: {} (${})\n",
        names[top],
        comma_int(salaries[top])
    ));

    out.push_str("\nDepartments with >2 employees:\n");
    for &di in &order {
        if dept_count[di] > 2 {
            out.push_str(&format!("  {} ({} employees)\n", dept_names[di], dept_count[di]));
        }
    }

    let total: i64 = salaries.iter().sum();
    out.push_str(&format!("\nTotal Budget: ${}", comma_int(total)));

    print!("{}", out);
}
