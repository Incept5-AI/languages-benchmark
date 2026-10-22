use std::process::Command;

fn run_cmd(program: &str, args: &[&str]) -> String {
    let output = Command::new(program)
        .args(args)
        .output()
        .expect("failed to execute command");
    let s = String::from_utf8_lossy(&output.stdout);
    s.trim_end().to_string()
}

fn main() {
    let os = run_cmd("uname", &["-s"]);
    let arch = run_cmd("uname", &["-m"]);
    let user = run_cmd("whoami", &[]);
    let host = run_cmd("hostname", &[]);

    print!("{{\n");
    print!("  \"hostname\": \"{}\",\n", host);
    print!("  \"os\": \"{}\",\n", os);
    print!("  \"architecture\": \"{}\",\n", arch);
    print!("  \"username\": \"{}\"\n", user);
    print!("}}");
}
