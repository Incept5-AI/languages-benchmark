import Foundation

func load(_ path: String) -> Any {
    let data = try! Data(contentsOf: URL(fileURLWithPath: path))
    return try! JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
}

func isBool(_ n: NSNumber) -> Bool { return String(cString: n.objCType) == "c" && CFGetTypeID(n) == CFBooleanGetTypeID() }

func equal(_ a: Any, _ b: Any) -> Bool {
    if let x = a as? [String: Any], let y = b as? [String: Any] {
        if x.count != y.count { return false }
        for (k, v) in x { guard let w = y[k], equal(v, w) else { return false } }
        return true
    }
    if let x = a as? [Any], let y = b as? [Any] {
        if x.count != y.count { return false }
        for i in 0..<x.count where !equal(x[i], y[i]) { return false }
        return true
    }
    if a is NSNull && b is NSNull { return true }
    if let x = a as? NSNumber, let y = b as? NSNumber { return isBool(x) == isBool(y) && x == y }
    if let x = a as? String, let y = b as? String { return x == y }
    return false
}

func merge(_ a: Any, _ b: Any) -> Any {
    if let x = a as? [String: Any], let y = b as? [String: Any] {
        var r = x
        for (k, v) in y { if let e = r[k] { r[k] = merge(e, v) } else { r[k] = v } }
        return r
    }
    if let x = a as? [Any], let y = b as? [Any] {
        var r: [Any] = []
        for v in x + y where !r.contains(where: { equal($0, v) }) { r.append(v) }
        return r
    }
    return b
}

func quote(_ s: String) -> String {
    var o = "\""
    for u in s.unicodeScalars {
        switch u {
        case "\"": o += "\\\""
        case "\\": o += "\\\\"
        case "\n": o += "\\n"
        case "\t": o += "\\t"
        case "\r": o += "\\r"
        default:
            if u.value < 0x20 { o += String(format: "\\u%04x", u.value) } else { o.unicodeScalars.append(u) }
        }
    }
    return o + "\""
}

func render(_ v: Any, _ ind: Int) -> String {
    let pad = String(repeating: "  ", count: ind + 1), end = String(repeating: "  ", count: ind)
    if let d = v as? [String: Any] {
        if d.isEmpty { return "{}" }
        let items = d.keys.sorted().map { pad + quote($0) + ": " + render(d[$0]!, ind + 1) }
        return "{\n" + items.joined(separator: ",\n") + "\n" + end + "}"
    }
    if let a = v as? [Any] {
        if a.isEmpty { return "[]" }
        return "[\n" + a.map { pad + render($0, ind + 1) }.joined(separator: ",\n") + "\n" + end + "]"
    }
    if v is NSNull { return "null" }
    if let s = v as? String { return quote(s) }
    if let n = v as? NSNumber {
        if isBool(n) { return n.boolValue ? "true" : "false" }
        let d = n.doubleValue
        if d == d.rounded() && abs(d) < 1e15 { return String(n.int64Value) }
        return "\(d)"
    }
    return "null"
}

let a = load("__FIXTURE_DIR__/config_a.json")
let b = load("__FIXTURE_DIR__/config_b.json")
print(render(merge(a, b), 0))
