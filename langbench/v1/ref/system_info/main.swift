import Foundation

func run(_ cmd: String, _ args: [String]) -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    p.arguments = [cmd] + args
    let pipe = Pipe()
    p.standardOutput = pipe
    try! p.run()
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
}
func q(_ s: String) -> String {
    return "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
}
let os = run("uname", ["-s"]), arch = run("uname", ["-m"]), user = run("whoami", []), host = run("hostname", [])
print("{\n  \"hostname\": \(q(host)),\n  \"os\": \(q(os)),\n  \"architecture\": \(q(arch)),\n  \"username\": \(q(user))\n}")
