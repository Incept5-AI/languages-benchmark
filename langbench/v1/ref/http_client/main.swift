import Foundation

let url = URL(string: "http://127.0.0.1:__PORT__/api/products")!
let data = try! Data(contentsOf: url)
let arr = try! JSONSerialization.jsonObject(with: data) as! [[String: Any]]
let filtered = arr.filter { ($0["price"] as! NSNumber).doubleValue > 50.0 }
    .sorted { ($0["name"] as! String) < ($1["name"] as! String) }
print("=== Filtered Products (price > $50.00) ===\n")
var total = 0.0
for p in filtered {
    let price = (p["price"] as! NSNumber).doubleValue
    total += price
    print("\(p["name"] as! String): $\(String(format: "%.2f", price))")
}
print("\nCount: \(filtered.count)")
print("Total: $\(String(format: "%.2f", total))")
