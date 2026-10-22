const A = [[1, 2, 3], [4, 5, 6], [7, 8, 9]];
const B = [[9, 8, 7], [6, 5, 4], [3, 2, 1]];
const n = 3;
const C: number[][] = [];
for (let i = 0; i < n; i++) {
  C.push([]);
  for (let j = 0; j < n; j++) {
    let s = 0;
    for (let k = 0; k < n; k++) s += A[i][k] * B[k][j];
    C[i].push(s);
  }
}
function det(m: number[][]): number {
  if (m.length === 1) return m[0][0];
  let s = 0;
  for (let j = 0; j < m.length; j++) {
    const minor = m.slice(1).map((r) => r.filter((_, c) => c !== j));
    s += (j % 2 === 0 ? 1 : -1) * m[0][j] * det(minor);
  }
  return s;
}
const show = (m: number[][]) => {
  for (const r of m) console.log("  " + r.map((v) => String(v).padStart(4)).join(" "));
};
console.log("=== Matrix Operations ===");
console.log("");
console.log("Matrix A:");
show(A);
console.log("");
console.log("Matrix B:");
show(B);
console.log("");
console.log("A x B =");
show(C);
console.log("");
console.log(`Determinant: ${det(C) + 0}`);
