import * as http from "http";

http.get("http://127.0.0.1:__PORT__/api/products", (res) => {
  const chunks: Buffer[] = [];
  res.on("data", (c) => chunks.push(c));
  res.on("end", () => {
    const data: any[] = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const sel = data
      .filter((p) => Number(p.price) > 50.0)
      .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    console.log("=== Filtered Products (price > $50.00) ===");
    console.log("");
    for (const p of sel) console.log(`${p.name}: $${Number(p.price).toFixed(2)}`);
    console.log("");
    console.log(`Count: ${sel.length}`);
    console.log(`Total: $${sel.reduce((s, p) => s + Number(p.price), 0).toFixed(2)}`);
  });
}).on("error", (e) => {
  console.error(e);
  process.exit(1);
});
