import * as fs from "fs";

type J = any;
const isObj = (x: J) => x !== null && typeof x === "object" && !Array.isArray(x);
const eq = (x: J, y: J) => JSON.stringify(sortKeys(x)) === JSON.stringify(sortKeys(y));

function sortKeys(v: J): J {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (isObj(v)) {
    const o: Record<string, J> = {};
    for (const k of Object.keys(v).sort()) o[k] = sortKeys(v[k]);
    return o;
  }
  return v;
}

function merge(a: J, b: J): J {
  if (isObj(a) && isObj(b)) {
    const r: Record<string, J> = {};
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (k in a && k in b) r[k] = merge(a[k], b[k]);
      else if (k in a) r[k] = a[k];
      else r[k] = b[k];
    }
    return r;
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    const out: J[] = [];
    for (const x of [...a, ...b]) if (!out.some((y) => eq(x, y))) out.push(x);
    return out;
  }
  return b;
}

const a = JSON.parse(fs.readFileSync("__FIXTURE_DIR__/config_a.json", "utf8"));
const b = JSON.parse(fs.readFileSync("__FIXTURE_DIR__/config_b.json", "utf8"));
console.log(JSON.stringify(sortKeys(merge(a, b)), null, 2));
