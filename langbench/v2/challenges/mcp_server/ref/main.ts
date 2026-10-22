import * as fs from "fs";

class Num { constructor(public raw: string, public isInt: boolean) {} }
class Raw { constructor(public s: string) {} }
const NULL = { isNull: true };
type J = any;
class PErr extends Error {}

function parse(s: string): J {
  let p = 0;
  const ws = () => { while (p < s.length) { const c = s[p]; if (c === " " || c === "\t" || c === "\n" || c === "\r") p++; else break; } };
  const fail = (): never => { throw new PErr(); };
  const value = (): J => {
    if (p >= s.length) fail();
    const c = s[p];
    if (c === "{") return obj();
    if (c === "[") return arr();
    if (c === '"') return str();
    if (c === "t") { lit("true"); return true; }
    if (c === "f") { lit("false"); return false; }
    if (c === "n") { lit("null"); return NULL; }
    if (c === "-" || (c >= "0" && c <= "9")) return num();
    return fail();
  };
  const lit = (w: string) => { if (!s.startsWith(w, p)) fail(); p += w.length; };
  const obj = (): J => {
    p++; const m = new Map<string, J>(); ws();
    if (s[p] === "}") { p++; return m; }
    for (;;) {
      ws(); if (s[p] !== '"') fail();
      const k = str(); ws();
      if (s[p] !== ":") fail();
      p++; ws(); const v = value(); m.delete(k); m.set(k, v); ws();
      if (p >= s.length) fail();
      const c = s[p++];
      if (c === "}") return m;
      if (c !== ",") fail();
    }
  };
  const arr = (): J => {
    p++; const a: J[] = []; ws();
    if (s[p] === "]") { p++; return a; }
    for (;;) {
      ws(); a.push(value()); ws();
      if (p >= s.length) fail();
      const c = s[p++];
      if (c === "]") return a;
      if (c !== ",") fail();
    }
  };
  const str = (): string => {
    p++; let b = "";
    for (;;) {
      if (p >= s.length) fail();
      const c = s[p++];
      if (c === '"') return b;
      if (c.charCodeAt(0) < 0x20) fail();
      if (c === "\\") {
        if (p >= s.length) fail();
        const e = s[p++];
        switch (e) {
          case '"': b += '"'; break; case "\\": b += "\\"; break; case "/": b += "/"; break;
          case "b": b += "\b"; break; case "f": b += "\f"; break; case "n": b += "\n"; break;
          case "r": b += "\r"; break; case "t": b += "\t"; break;
          case "u": {
            const h = s.substr(p, 4);
            if (!/^[0-9a-fA-F]{4}$/.test(h)) fail();
            p += 4; b += String.fromCharCode(parseInt(h, 16)); break;
          }
          default: fail();
        }
      } else b += c;
    }
  };
  const num = (): J => {
    const m = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?/.exec(s.slice(p));
    if (!m) return fail();
    p += m[0].length;
    return new Num(m[0], m[2] === undefined && m[3] === undefined);
  };
  ws(); const v = value(); ws(); if (p !== s.length) fail();
  return v;
}

function serStr(s: string): string {
  let o = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x22) o += '\\"';
    else if (c === 0x5c) o += "\\\\";
    else if (c < 0x20 || c > 0x7e) o += "\\u" + c.toString(16).padStart(4, "0");
    else o += s[i];
  }
  return o + '"';
}
function ser(v: J): string {
  if (v === NULL || v === null || v === undefined) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (v instanceof Num) return v.raw;
  if (v instanceof Raw) return v.s;
  if (typeof v === "number" || typeof v === "bigint") return String(v);
  if (typeof v === "string") return serStr(v);
  if (Array.isArray(v)) return "[" + v.map(ser).join(",") + "]";
  if (v instanceof Map) return "{" + [...v.entries()].map(([k, x]) => serStr(k) + ":" + ser(x)).join(",") + "}";
  return "{" + Object.entries(v).map(([k, x]) => serStr(k) + ":" + ser(x)).join(",") + "}";
}

function cmpCp(a: string, b: string): number {
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    const ca = a.codePointAt(i)!, cb = b.codePointAt(j)!;
    if (ca !== cb) return ca < cb ? -1 : 1;
    i += ca > 0xffff ? 2 : 1; j += cb > 0xffff ? 2 : 1;
  }
  const ra = a.length - i, rb = b.length - j;
  return ra === rb ? 0 : ra < rb ? -1 : 1;
}

const TOOLS = String.raw`[{"name":"kv_set","title":"Set a value","description":"Store a string value under a key, replacing any previous value.","inputSchema":{"type":"object","properties":{"key":{"type":"string","minLength":1},"value":{"type":"string"}},"required":["key","value"],"additionalProperties":false}},{"name":"kv_get","title":"Get a value","description":"Return the value stored under a key.","inputSchema":{"type":"object","properties":{"key":{"type":"string","minLength":1}},"required":["key"],"additionalProperties":false}},{"name":"kv_list","title":"List keys","description":"List the stored keys that start with a prefix, sorted by Unicode code point.","inputSchema":{"type":"object","properties":{"prefix":{"type":"string"}},"additionalProperties":false},"outputSchema":{"type":"object","properties":{"keys":{"type":"array","items":{"type":"string"}},"count":{"type":"integer"}},"required":["keys","count"]}},{"name":"sum","title":"Sum integers","description":"Add up a non-empty list of integers.","inputSchema":{"type":"object","properties":{"numbers":{"type":"array","items":{"type":"integer"},"minItems":1}},"required":["numbers"],"additionalProperties":false},"outputSchema":{"type":"object","properties":{"sum":{"type":"integer"},"count":{"type":"integer"}},"required":["sum","count"]},"annotations":{"readOnlyHint":true,"idempotentHint":true}},{"name":"text_stats","title":"Text statistics","description":"Count the words, Unicode code points and UTF-8 bytes of a text.","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false},"outputSchema":{"type":"object","properties":{"words":{"type":"integer"},"codepoints":{"type":"integer"},"utf8_bytes":{"type":"integer"}},"required":["words","codepoints","utf8_bytes"]},"annotations":{"readOnlyHint":true}}]`;
let initialized = false;
const kv = new Map<string, string>();

const errorResp = (id: J, code: number, msg: string) => '{"jsonrpc":"2.0","id":' + ser(id) + ',"error":{"code":' + code + ',"message":' + serStr(msg) + "}}";
const resultResp = (id: J, r: J) => '{"jsonrpc":"2.0","id":' + ser(id) + ',"result":' + ser(r) + "}";
const textResult = (text: string, isError: boolean) => ({ content: [{ type: "text", text }], isError });
const structResult = (S: J) => ({ content: [{ type: "text", text: ser(S) }], structuredContent: S, isError: false });

type Prop = [string, "string" | "intarray", boolean, boolean];
const SPECS: Record<string, Prop[]> = {
  kv_set: [["key", "string", true, true], ["value", "string", true, false]],
  kv_get: [["key", "string", true, true]],
  kv_list: [["prefix", "string", false, false]],
  sum: [["numbers", "intarray", true, true]],
  text_stats: [["text", "string", true, false]],
};

function callTool(name: string, argsObj: J): J {
  const spec = SPECS[name];
  let args: Map<string, J>;
  if (argsObj === undefined) args = new Map();
  else if (argsObj instanceof Map) args = argsObj;
  else return textResult("arguments must be an object", true);
  let unknown: string | null = null;
  for (const k of args.keys()) {
    if (!spec.some((pr) => pr[0] === k) && (unknown === null || cmpCp(k, unknown) < 0)) unknown = k;
  }
  if (unknown !== null) return textResult("unknown argument: " + unknown, true);
  for (const [pn, ty, req, nonEmpty] of spec) {
    if (!args.has(pn)) { if (req) return textResult("missing required argument: " + pn, true); continue; }
    const v = args.get(pn);
    if (ty === "string") {
      if (typeof v !== "string") return textResult("argument " + pn + " must be a string", true);
      if (nonEmpty && v.length === 0) return textResult("argument " + pn + " must not be empty", true);
    } else {
      if (!Array.isArray(v) || !v.every((x) => x instanceof Num && x.isInt)) return textResult("argument " + pn + " must be an array of integers", true);
      if (nonEmpty && v.length === 0) return textResult("argument " + pn + " must not be empty", true);
    }
  }
  switch (name) {
    case "kv_set": {
      const k = args.get("key"), v = args.get("value");
      const existed = kv.has(k);
      kv.set(k, v);
      return textResult((existed ? "updated " : "created ") + k, false);
    }
    case "kv_get": {
      const k = args.get("key");
      if (!kv.has(k)) return textResult("key not found: " + k, true);
      return textResult(kv.get(k)!, false);
    }
    case "kv_list": {
      const pre: string = args.has("prefix") ? args.get("prefix") : "";
      const keys = [...kv.keys()].filter((k) => k.startsWith(pre)).sort(cmpCp);
      return structResult({ keys, count: keys.length });
    }
    case "sum": {
      const a: Num[] = args.get("numbers");
      let sum = 0n;
      for (const x of a) sum += BigInt(x.raw);
      return structResult({ sum, count: a.length });
    }
    default: {
      const t: string = args.get("text");
      let words = 0, cps = 0, bytes = 0, inw = false;
      for (let i = 0; i < t.length; ) {
        const cp = t.codePointAt(i)!; i += cp > 0xffff ? 2 : 1;
        cps++;
        bytes += cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4;
        const sep = cp === 32 || cp === 9 || cp === 10 || cp === 13;
        if (!sep && !inw) words++;
        inw = !sep;
      }
      return structResult({ words, codepoints: cps, utf8_bytes: bytes });
    }
  }
}

function handle(line: string): string | null {
  let msg: J;
  try { msg = parse(line); } catch (e) { return errorResp(NULL, -32700, "Parse error"); }
  if (!(msg instanceof Map)) return errorResp(NULL, -32600, "Invalid Request");
  const m: Map<string, J> = msg;
  if (!m.has("method") && (m.has("result") || m.has("error"))) return null;
  const hasId = m.has("id");
  const id = m.get("id");
  const idOk = hasId && (typeof id === "string" || (id instanceof Num && id.isInt));
  if (m.get("jsonrpc") !== "2.0" || typeof m.get("method") !== "string" || (hasId && !idOk))
    return errorResp(idOk ? id : NULL, -32600, "Invalid Request");
  if (!hasId) return null;
  const method: string = m.get("method");
  if (!["initialize", "ping", "tools/list", "tools/call"].includes(method)) return errorResp(id, -32601, "Method not found");
  const hasParams = m.has("params");
  const params = m.get("params");
  if (hasParams && !(params instanceof Map)) return errorResp(id, -32602, "Invalid params");
  const pm: Map<string, J> | null = hasParams ? params : null;
  if (!initialized && method !== "initialize" && method !== "ping") return errorResp(id, -32002, "Server not initialized");
  switch (method) {
    case "initialize": {
      if (initialized) return errorResp(id, -32600, "Already initialized");
      if (pm === null || typeof pm.get("protocolVersion") !== "string") return errorResp(id, -32602, "Invalid params");
      let v: string = pm.get("protocolVersion");
      if (!["2025-06-18", "2025-03-26", "2024-11-05"].includes(v)) v = "2025-06-18";
      initialized = true;
      return resultResp(id, new Raw('{"protocolVersion":' + serStr(v) + ',"capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"cb2-kv","title":"CB2 key-value server","version":"1.4.2"}}'));
    }
    case "ping": return resultResp(id, new Raw("{}"));
    case "tools/list": return resultResp(id, new Raw('{"tools":' + TOOLS + "}"));
    default: {
      if (pm === null || typeof pm.get("name") !== "string") return errorResp(id, -32602, "Invalid params");
      const name: string = pm.get("name");
      if (!Object.prototype.hasOwnProperty.call(SPECS, name)) return errorResp(id, -32602, "Unknown tool: " + name);
      const a = pm.has("arguments") ? pm.get("arguments") : undefined;
      return resultResp(id, callTool(name, a));
    }
  }
}

const data = fs.readFileSync(0);
const out: string[] = [];
let st = 0;
for (let i = 0; i <= data.length; i++) {
  if (i === data.length || data[i] === 10) {
    if (i === data.length && st === i) break;
    const line = data.subarray(st, i).toString("utf8");
    st = i + 1;
    if (/^[ \t\r]*$/.test(line)) continue;
    const r = handle(line);
    if (r !== null) out.push(r + "\n");
  }
}
process.stdout.write(out.join(""));
process.exitCode = 0;
