import * as http from "node:http";
import * as fs from "node:fs";
import * as path from "node:path";

interface Resp { status: number; headers: http.IncomingHttpHeaders; body: string; }

const cfg = JSON.parse(fs.readFileSync(path.join(process.argv[2], "client.json"), "utf8"));
const base = new URL(process.argv[3]);
let basePath = base.pathname.replace(/\/$/, "");

function request(method: string, p: string, headers: Record<string, string>, body?: Buffer): Promise<Resp> {
  return new Promise((resolve, reject) => {
    const h: Record<string, string> = { ...headers, Connection: "close" };
    if (body) h["Content-Length"] = String(body.length);
    const req = http.request({
      host: base.hostname, port: base.port ? Number(base.port) : 80, method, path: p, headers: h, agent: false,
    }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (c: Buffer) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode!, headers: res.headers, body: Buffer.concat(chunks).toString("utf8") }));
      res.on("error", reject);
    });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function formEnc(v: string): string {
  let out = "";
  for (const b of Buffer.from(v, "utf8")) {
    const c = String.fromCharCode(b);
    if (/[A-Za-z0-9\-._*]/.test(c)) out += c;
    else if (b === 0x20) out += "+";
    else out += "%" + b.toString(16).toUpperCase().padStart(2, "0");
  }
  return out;
}

function pctEnc(v: string): string {
  let out = "";
  for (const b of Buffer.from(v, "utf8")) {
    const c = String.fromCharCode(b);
    if (/[A-Za-z0-9\-._~]/.test(c)) out += c;
    else out += "%" + b.toString(16).toUpperCase().padStart(2, "0");
  }
  return out;
}

class Fatal extends Error {}

async function getToken(): Promise<string> {
  const basic = Buffer.from(formEnc(cfg.client_id) + ":" + formEnc(cfg.client_secret), "utf8").toString("base64");
  const r = await request("POST", basePath + "/oauth/token", {
    Authorization: "Basic " + basic,
    "Content-Type": "application/x-www-form-urlencoded",
    Accept: "application/json",
  }, Buffer.from("grant_type=client_credentials&scope=" + formEnc(cfg.scope), "utf8"));
  if (r.status !== 200) throw new Fatal(`ERROR ${r.status} ${r.body}`);
  return JSON.parse(r.body).access_token;
}

function cents(a: string): bigint {
  let neg = false;
  if (a.startsWith("-")) { neg = true; a = a.slice(1); }
  let [ip, fp = ""] = a.split(".");
  fp = fp.padEnd(2, "0");
  const v = BigInt(ip) * 100n + BigInt(fp);
  return neg ? -v : v;
}

function fmt(c: bigint): string {
  const neg = c < 0n;
  const a = neg ? -c : c;
  return (neg ? "-" : "") + (a / 100n).toString() + "." + (a % 100n).toString().padStart(2, "0");
}

function cmpBytes(a: string, b: string): number {
  return Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const totals = new Map<string, { name: string; cur: string; sum: bigint }>();
  const statuses = new Map<string, number>();
  let n = 0, pages = 0, retries = 0, reauth = 0;
  try {
    let token = await getToken();
    let fresh = true;
    let cursor: string | null = null;
    while (true) {
      const p = basePath + "/orders?limit=" + cfg.page_size + (cursor !== null ? "&cursor=" + pctEnc(cursor) : "");
      const r = await request("GET", p, { Authorization: "Bearer " + token, Accept: "application/json" });
      const wasFresh = fresh;
      fresh = false;
      if (r.status === 200) {
        pages++;
        const m = JSON.parse(r.body);
        for (const o of (m.data ?? [])) {
          n++;
          statuses.set(o.status, (statuses.get(o.status) ?? 0) + 1);
          if (o.status !== "cancelled") {
            const name: string = o.customer.name, cur: string = o.currency;
            const k = name + "\u0000" + cur;
            let e = totals.get(k);
            if (!e) { e = { name, cur, sum: 0n }; totals.set(k, e); }
            e.sum += cents(o.amount);
          }
        }
        if (m.next_cursor === null || m.next_cursor === undefined) break;
        cursor = m.next_cursor;
      } else if (r.status === 429) {
        retries++;
        let secs = parseInt(String(r.headers["retry-after"] ?? "1").trim(), 10);
        if (isNaN(secs)) secs = 1;
        await sleep(secs * 1000 + 50);
      } else if (r.status === 503) {
        retries++;
        await sleep(100);
      } else if (r.status === 401 && !wasFresh) {
        token = await getToken();
        reauth++;
        fresh = true;
      } else {
        throw new Fatal(`ERROR ${r.status} ${r.body}`);
      }
    }
  } catch (e) {
    if (e instanceof Fatal) {
      process.stdout.write(e.message + "\n");
      return;
    }
    throw e;
  }
  const es = [...totals.values()].sort((x, y) => cmpBytes(x.name, y.name) || cmpBytes(x.cur, y.cur));
  let out = "";
  for (const e of es) out += `total ${e.cur} ${fmt(e.sum)} ${e.name}\n`;
  for (const s of [...statuses.keys()].sort()) out += `status ${s} ${statuses.get(s)}\n`;
  out += `orders ${n} pages ${pages} retries ${retries} reauth ${reauth}\n`;
  process.stdout.write(out);
}

main();
