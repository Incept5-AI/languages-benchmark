import * as fs from "node:fs";
import * as path from "node:path";
import * as net from "node:net";

class Num { constructor(public t: string) {} }
type J = null | boolean | Num | string | J[] | Map<string, J>;

function parse(s: string): J {
  let i = 0;
  const ws = () => { while (i < s.length && " \t\n\r".includes(s[i])) i++; };
  const str = (): string => {
    i++;
    let b = "";
    while (true) {
      const c = s[i++];
      if (c === '"') return b;
      if (c === "\\") {
        const e = s[i++];
        switch (e) {
          case "b": b += "\b"; break;
          case "f": b += "\f"; break;
          case "n": b += "\n"; break;
          case "r": b += "\r"; break;
          case "t": b += "\t"; break;
          case "u": b += String.fromCharCode(parseInt(s.substr(i, 4), 16)); i += 4; break;
          default: b += e;
        }
      } else b += c;
    }
  };
  const val = (): J => {
    ws();
    const c = s[i];
    if (c === "{") {
      i++; const m = new Map<string, J>(); ws();
      if (s[i] === "}") { i++; return m; }
      while (true) {
        ws(); const k = str(); ws(); i++;
        m.set(k, val()); ws();
        const d = s[i++];
        if (d === "}") return m;
      }
    }
    if (c === "[") {
      i++; const a: J[] = []; ws();
      if (s[i] === "]") { i++; return a; }
      while (true) {
        a.push(val()); ws();
        const d = s[i++];
        if (d === "]") return a;
      }
    }
    if (c === '"') return str();
    if (s.startsWith("true", i)) { i += 4; return true; }
    if (s.startsWith("false", i)) { i += 5; return false; }
    if (s.startsWith("null", i)) { i += 4; return null; }
    const st = i;
    while (i < s.length && "+-0123456789.eE".includes(s[i])) i++;
    return new Num(s.slice(st, i));
  };
  return val();
}

function esc(s: string): string {
  let b = '"';
  for (const ch of s) {
    const c = ch.codePointAt(0)!;
    if (ch === '"') b += '\\"';
    else if (ch === "\\") b += "\\\\";
    else if (c === 8) b += "\\b";
    else if (c === 12) b += "\\f";
    else if (c === 10) b += "\\n";
    else if (c === 13) b += "\\r";
    else if (c === 9) b += "\\t";
    else if (c < 0x20) b += "\\u" + c.toString(16).padStart(4, "0");
    else b += ch;
  }
  return b + '"';
}

function write(v: J): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (v instanceof Num) return v.t;
  if (typeof v === "string") return esc(v);
  if (Array.isArray(v)) return "[" + v.map(write).join(",") + "]";
  const parts: string[] = [];
  for (const [k, x] of v) parts.push(esc(k) + ":" + write(x));
  return "{" + parts.join(",") + "}";
}

function get(v: J | undefined, k: string): J | undefined {
  return v instanceof Map ? v.get(k) : undefined;
}
function toBig(v: J | undefined): bigint {
  if (v instanceof Num) {
    if (/^-?\d+$/.test(v.t)) return BigInt(v.t);
    return BigInt(Math.trunc(Number(v.t)));
  }
  return 0n;
}

function csv(s: string): string[][] {
  if (s.charCodeAt(0) === 0xfeff) s = s.slice(1);
  const rows: string[][] = [];
  let i = 0;
  const n = s.length;
  while (i < n) {
    const row: string[] = [];
    while (true) {
      let f = "";
      if (i < n && s[i] === '"') {
        i++;
        while (i < n) {
          if (s[i] === '"') {
            if (i + 1 < n && s[i + 1] === '"') { f += '"'; i += 2; }
            else { i++; break; }
          } else { f += s[i]; i++; }
        }
      }
      while (i < n && s[i] !== "," && s[i] !== "\r" && s[i] !== "\n") { f += s[i]; i++; }
      row.push(f);
      if (i < n && s[i] === ",") { i++; continue; }
      if (i < n && s[i] === "\r") i++;
      if (i < n && s[i] === "\n") i++;
      break;
    }
    rows.push(row);
  }
  return rows;
}

function post(host: string, port: number, hostHdr: string, p: string, key: string, body: string): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const bb = Buffer.from(body, "utf8");
    const sock = net.connect(port, host);
    const chunks: Buffer[] = [];
    sock.on("connect", () => {
      sock.write(
        `POST ${p} HTTP/1.1\r\nHost: ${hostHdr}\r\nAuthorization: Bearer ${key}\r\nContent-Type: application/json\r\nContent-Length: ${bb.length}\r\nConnection: close\r\n\r\n`
      );
      sock.write(bb);
    });
    sock.on("data", (d: Buffer) => chunks.push(d));
    sock.on("error", reject);
    sock.on("end", () => {
      const all = Buffer.concat(chunks);
      const sep = all.indexOf("\r\n\r\n");
      const head = all.subarray(0, sep).toString("latin1");
      const lines = head.split("\r\n");
      const status = parseInt(lines[0].split(" ")[1], 10);
      let cl = -1;
      for (const l of lines.slice(1)) {
        const c = l.indexOf(":");
        if (c > 0 && l.slice(0, c).trim().toLowerCase() === "content-length") cl = parseInt(l.slice(c + 1).trim(), 10);
      }
      const start = sep + 4;
      const end = cl >= 0 ? Math.min(all.length, start + cl) : all.length;
      resolve({ status, body: all.subarray(start, end).toString("utf8") });
      sock.destroy();
    });
  });
}

async function main() {
  const dir = process.argv[2];
  const base = process.argv[3];
  const rest = base.slice(base.indexOf("://") + 3);
  const sl = rest.indexOf("/");
  const hp = sl >= 0 ? rest.slice(0, sl) : rest;
  const prefix = sl >= 0 ? rest.slice(sl) : "";
  const co = hp.lastIndexOf(":");
  const host = co >= 0 ? hp.slice(0, co) : hp;
  const port = co >= 0 ? parseInt(hp.slice(co + 1), 10) : 80;

  const task = parse(fs.readFileSync(path.join(dir, "task.json"), "utf8"));
  const rates = get(parse(fs.readFileSync(path.join(dir, "rates.json"), "utf8")), "rates");
  const rows = csv(fs.readFileSync(path.join(dir, "inventory.csv")).toString("utf8"));
  const inv = new Map<string, string[]>();
  for (const r of rows.slice(1)) if (r.length >= 5) inv.set(r[0], r);

  const key = get(task, "api_key") as string;
  const messages: J[] = [
    new Map<string, J>([["role", "system"], ["content", get(task, "system") ?? null]]),
    new Map<string, J>([["role", "user"], ["content", get(task, "user") ?? null]]),
  ];
  const out: string[] = [];
  let pt = 0n, ct = 0n, tt = 0n, requests = 0;
  const normInt = (s: string) => BigInt(s.trim()).toString();

  while (true) {
    const req = new Map<string, J>([
      ["model", get(task, "model") ?? null],
      ["messages", messages],
      ["tools", get(task, "tools") ?? []],
      ["tool_choice", "auto"],
    ]);
    const r = await post(host, port, hp, prefix + "/chat/completions", key, write(req));
    requests++;
    if (r.status !== 200) {
      let m = "";
      try {
        const x = get(get(parse(r.body), "error"), "message");
        if (typeof x === "string") m = x;
      } catch {}
      out.push(`ERROR ${r.status} ${m}`);
      process.stdout.write(out.join("\n") + "\n");
      return;
    }
    const resp = parse(r.body);
    const u = get(resp, "usage");
    if (u instanceof Map) {
      pt += toBig(u.get("prompt_tokens"));
      ct += toBig(u.get("completion_tokens"));
      tt += toBig(u.get("total_tokens"));
    }
    const choices = get(resp, "choices") as J[];
    const choice = choices[0];
    const msg = get(choice, "message");
    const fr = get(choice, "finish_reason");
    if (fr === "tool_calls") {
      messages.push(msg as J);
      const content = get(msg, "content");
      if (typeof content === "string") out.push("SAY " + content);
      const calls = (get(msg, "tool_calls") as J[]) ?? [];
      const results: [string, string][] = [];
      for (const call of calls) {
        const id = get(call, "id") as string;
        const fn = get(call, "function");
        const name = get(fn, "name") as string;
        const argstr = (get(fn, "arguments") as string) ?? "{}";
        out.push(`CALL ${id} ${name}`);
        let res: string;
        if (name === "inventory_lookup") {
          const a = parse(argstr);
          const sku = get(a, "sku") as string;
          const row = inv.get(sku);
          if (!row) res = `{"error":${esc("unknown sku: " + sku)}}`;
          else
            res = `{"sku":${esc(row[0])},"name":${esc(row[1])},"price_cents":${normInt(row[2])},"currency":${esc(row[3])},"stock":${normInt(row[4])}}`;
        } else if (name === "convert_currency") {
          const a = parse(argstr);
          const amt = toBig(get(a, "amount_cents"));
          const from = get(a, "from") as string;
          const to = get(a, "to") as string;
          const rf = toBig(get(rates, from));
          const rt = toBig(get(rates, to));
          const conv = (2n * amt * rt + rf) / (2n * rf);
          res = `{"amount_cents":${conv.toString()},"currency":${esc(to)}}`;
        } else {
          res = `{"error":${esc("unknown tool: " + name)}}`;
        }
        messages.push(new Map<string, J>([["role", "tool"], ["tool_call_id", id], ["content", res]]));
        results.push([id, res]);
      }
      for (const [id, res] of results) out.push(`RESULT ${id} ${res}`);
    } else {
      const content = get(msg, "content");
      out.push("FINAL " + (typeof content === "string" ? content : ""));
      break;
    }
  }
  out.push(`USAGE prompt=${pt} completion=${ct} total=${tt} requests=${requests}`);
  process.stdout.write(out.join("\n") + "\n");
}

main();
