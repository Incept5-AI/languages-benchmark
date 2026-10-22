import * as fs from "fs";
import * as path from "path";
import * as dgram from "dgram";
import * as net from "net";
import { randomInt } from "crypto";

const u16 = (m: Buffer, p: number) => (m[p] << 8) | m[p + 1];

function readName(m: Buffer, pos: number): [string, number] {
  const labels: string[] = [];
  let end = -1;
  let jumps = 0;
  while (pos < m.length) {
    const l = m[pos];
    if ((l & 0xc0) === 0xc0) {
      if (pos + 1 >= m.length) break;
      if (end < 0) end = pos + 2;
      if (++jumps > 255) break;
      pos = ((l & 0x3f) << 8) | m[pos + 1];
      continue;
    }
    if (l === 0) {
      if (end < 0) end = pos + 1;
      break;
    }
    labels.push(m.subarray(pos + 1, Math.min(pos + 1 + l, m.length)).toString("latin1").toLowerCase());
    pos += 1 + l;
  }
  return [labels.length ? labels.join(".") : ".", end < 0 ? m.length : end];
}

const TYPES: Record<string, number> = { A: 1, CNAME: 5, MX: 15, TXT: 16, AAAA: 28 };
const typeName = (t: number) => {
  for (const k of Object.keys(TYPES)) if (TYPES[k] === t) return k;
  return "TYPE" + t;
};

function aaaa(d: Buffer): string {
  const g: number[] = [];
  for (let i = 0; i < 8; i++) g.push(u16(d, 2 * i));
  let bs = -1, bl = 0;
  for (let i = 0; i < 8; ) {
    if (g[i] === 0) {
      let j = i;
      while (j < 8 && g[j] === 0) j++;
      if (j - i > bl) { bl = j - i; bs = i; }
      i = j;
    } else i++;
  }
  const h = (a: number[]) => a.map((x) => x.toString(16)).join(":");
  if (bl < 2) return h(g);
  return h(g.slice(0, bs)) + "::" + h(g.slice(bs + bl));
}

function txt(d: Buffer): string {
  const parts: string[] = [];
  let p = 0;
  while (p < d.length) {
    const l = d[p++];
    let s = '"';
    for (let k = 0; k < l && p < d.length; k++, p++) {
      const b = d[p];
      if (b === 0x22) s += '\\"';
      else if (b === 0x5c) s += "\\\\";
      else if (b >= 0x20 && b <= 0x7e) s += String.fromCharCode(b);
      else s += "\\" + b.toString().padStart(3, "0");
    }
    parts.push(s + '"');
  }
  return parts.join(" ");
}

function udpQuery(sock: dgram.Socket, q: Buffer, id: number, port: number): Promise<Buffer> {
  return new Promise((resolve) => {
    const onMsg = (m: Buffer) => {
      if (m.length >= 12 && u16(m, 0) === id && (m[2] & 0x80)) {
        sock.off("message", onMsg);
        resolve(Buffer.from(m));
      }
    };
    sock.on("message", onMsg);
    sock.send(q, port, "127.0.0.1");
  });
}

function tcpQuery(q: Buffer, port: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const s = net.connect(port, "127.0.0.1");
    let acc = Buffer.alloc(0);
    let done = false;
    s.on("connect", () => {
      const len = Buffer.alloc(2);
      len.writeUInt16BE(q.length);
      s.write(Buffer.concat([len, q]));
    });
    s.on("data", (d: Buffer) => {
      acc = Buffer.concat([acc, d]);
      if (!done && acc.length >= 2) {
        const n = u16(acc, 0);
        if (acc.length >= 2 + n) {
          done = true;
          resolve(acc.subarray(2, 2 + n));
          s.destroy();
        }
      }
    });
    s.on("error", (e) => { if (!done) reject(e); });
  });
}

async function main() {
  const [dir, portStr] = process.argv.slice(2);
  const port = parseInt(portStr, 10);
  const lines = fs.readFileSync(path.join(dir, "queries.txt"), "latin1").split("\n");
  const sock = dgram.createSocket("udp4");
  await new Promise<void>((r) => sock.bind(0, "127.0.0.1", () => r()));
  const out: string[] = [];
  const RN = ["NOERROR", "FORMERR", "SERVFAIL", "NXDOMAIN", "NOTIMP", "REFUSED"];
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const parts = line.split(/\s+/);
    let name = parts[0];
    const ty = parts[1] ?? "A";
    const qtype = TYPES[ty] ?? 1;
    if (name.endsWith(".")) name = name.slice(0, -1);
    const id = randomInt(65536);
    const bytes: number[] = [id >> 8, id & 0xff, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0];
    if (name) {
      for (const lab of name.split(".")) {
        const b = Buffer.from(lab, "latin1");
        bytes.push(b.length, ...b);
      }
    }
    bytes.push(0, qtype >> 8, qtype & 0xff, 0, 1);
    const q = Buffer.from(bytes);
    let msg = await udpQuery(sock, q, id, port);
    if (msg[2] & 0x02) msg = await tcpQuery(q, port);
    const rc = msg[3] & 0x0f;
    const lname = name.toLowerCase();
    out.push(`${lname || "."} ${ty} ${rc <= 5 ? RN[rc] : "RCODE" + rc}`);
    const qd = u16(msg, 4), an = u16(msg, 6);
    let pos = 12;
    for (let i = 0; i < qd; i++) pos = readName(msg, pos)[1] + 4;
    for (let i = 0; i < an; i++) {
      const [owner, p] = readName(msg, pos);
      pos = p;
      const t = u16(msg, pos);
      const ttl = msg.readUInt32BE(pos + 4);
      const rdl = u16(msg, pos + 8);
      const rs = pos + 10;
      const rd = msg.subarray(rs, rs + rdl);
      let data: string;
      if (t === 1 && rdl === 4) data = Array.from(rd).join(".");
      else if (t === 28 && rdl === 16) data = aaaa(rd);
      else if (t === 5) data = readName(msg, rs)[0];
      else if (t === 15) data = `${u16(msg, rs)} ${readName(msg, rs + 2)[0]}`;
      else if (t === 16) data = txt(rd);
      else data = `\\# ${rdl}` + (rdl ? " " + rd.toString("hex") : "");
      out.push(`  ${owner} ${ttl} ${typeName(t)} ${data}`);
      pos = rs + rdl;
    }
  }
  sock.close();
  process.stdout.write(out.length ? out.join("\n") + "\n" : "");
}

main();
