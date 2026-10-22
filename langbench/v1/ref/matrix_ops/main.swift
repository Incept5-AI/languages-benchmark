let a = [[1,2,3],[4,5,6],[7,8,9]]
let b = [[9,8,7],[6,5,4],[3,2,1]]
var c = [[Int]](repeating: [Int](repeating: 0, count: 3), count: 3)
for i in 0..<3 { for j in 0..<3 { for k in 0..<3 { c[i][j] += a[i][k] * b[k][j] } } }
func det(_ m: [[Int]]) -> Int {
    return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
         - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
         + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
}
func fmt(_ m: [[Int]]) {
    for r in m {
        print("  " + r.map { let s = String($0); return String(repeating: " ", count: max(0, 4 - s.count)) + s }.joined(separator: " "))
    }
}
print("=== Matrix Operations ===\n")
print("Matrix A:"); fmt(a)
print("\nMatrix B:"); fmt(b)
print("\nA x B ="); fmt(c)
print("\nDeterminant: \(det(c))")
