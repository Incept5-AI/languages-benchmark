import * as net from "node:net";
import * as crypto from "node:crypto";

const outLines: string[] = [];
function println(s: string) {
  outLines.push(s + "\n");
}

function encodeFrame(op: number, payload: Buffer): Buffer {
  const len = payload.length;
  let hdr: Buffer;
  if (len <= 125) {
    hdr = Buffer.from([0x80 | op, 0x80 | len]);
  } else if (len <= 65535) {
    hdr = Buffer.alloc(4);
    hdr[0] = 0x80 | op;
    hdr[1] = 0x80 | 126;
    hdr.writeUInt16BE(len, 2);
  } else {
    hdr = Buffer.alloc(10);
    hdr[0] = 0x80 | op;
    hdr[1] = 0x80 | 127;
    hdr.writeBigUInt64BE(BigInt(len), 2);
  }
  let mk: Buffer;
  do {
    mk = crypto.randomBytes(4);
  } while (mk[0] === 0 && mk[1] === 0 && mk[2] === 0 && mk[3] === 0);
  const body = Buffer.alloc(len);
  for (let i = 0; i < len; i++) body[i] = payload[i] ^ mk[i & 3];
  return Buffer.concat([hdr, mk, body]);
}

function session(url: string): Promise<void> {
  return new Promise((resolve) => {
    let rest = url.startsWith("ws://") ? url.slice(5) : url;
    const slash = rest.indexOf("/");
    const hostport = slash < 0 ? rest : rest.slice(0, slash);
    const target = slash < 0 ? "/" : rest.slice(slash);
    const colon = hostport.lastIndexOf(":");
    const host = colon < 0 ? hostport : hostport.slice(0, colon);
    const port = colon < 0 ? 80 : parseInt(hostport.slice(colon + 1), 10);
    const key = crypto.randomBytes(16).toString("base64");
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      sock.destroy();
      resolve();
    };
    const sock = net.connect({ host, port }, () => {
      sock.setNoDelay(true);
      sock.write(
        `GET ${target} HTTP/1.1\r\nHost: ${hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n` +
          `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`,
      );
    });
    let buf = Buffer.alloc(0);
    let handshaken = false;
    let closeSent = false;
    let pendingSha = false;
    let msgOp = -1;
    let parts: Buffer[] = [];
    const send = (op: number, p: Buffer) => {
      sock.write(encodeFrame(op, p));
    };

    const pump = () => {
      if (!handshaken) {
        const idx = buf.indexOf("\r\n\r\n");
        if (idx < 0) return;
        const head = buf.subarray(0, idx).toString("latin1");
        buf = buf.subarray(idx + 4);
        const lines = head.split(/\r?\n/);
        const status = lines[0];
        const sp1 = status.indexOf(" ");
        const after = sp1 < 0 ? "" : status.slice(sp1 + 1);
        const sp2 = after.indexOf(" ");
        const code = sp2 < 0 ? after : after.slice(0, sp2);
        const reason = sp2 < 0 ? "" : after.slice(sp2 + 1);
        if (code !== "101") {
          println(`handshake failed: ${code} ${reason}`);
          finish();
          return;
        }
        let accept: string | null = null;
        for (const l of lines.slice(1)) {
          const c = l.indexOf(":");
          if (c < 0) continue;
          if (l.slice(0, c).trim().toLowerCase() === "sec-websocket-accept") accept = l.slice(c + 1).trim();
        }
        const expect = crypto
          .createHash("sha1")
          .update(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11", "latin1")
          .digest("base64");
        if (accept !== expect) {
          println("handshake failed: bad Sec-WebSocket-Accept");
          finish();
          return;
        }
        handshaken = true;
      }
      while (!done) {
        if (buf.length < 2) return;
        const b0 = buf[0],
          b1 = buf[1];
        let off = 2;
        let len = b1 & 0x7f;
        if (len === 126) {
          if (buf.length < 4) return;
          len = buf.readUInt16BE(2);
          off = 4;
        } else if (len === 127) {
          if (buf.length < 10) return;
          len = Number(buf.readBigUInt64BE(2));
          off = 10;
        }
        const masked = (b1 & 0x80) !== 0;
        if (masked) off += 4;
        if (buf.length < off + len) return;
        const payload = Buffer.from(buf.subarray(off, off + len));
        if (masked) {
          const mk = buf.subarray(off - 4, off);
          for (let i = 0; i < len; i++) payload[i] ^= mk[i & 3];
        }
        buf = buf.subarray(off + len);
        const fin = (b0 & 0x80) !== 0;
        const op = b0 & 0x0f;
        if (op === 9) {
          send(10, payload);
          continue;
        }
        if (op === 10) continue;
        if (op === 8) {
          if (payload.length >= 2) {
            const cc = payload.readUInt16BE(0);
            const r = payload.subarray(2).toString("utf8");
            println(r === "" ? `closed ${cc}` : `closed ${cc} ${r}`);
          } else println("closed 1005");
          if (!closeSent) {
            closeSent = true;
            sock.end(encodeFrame(8, payload.subarray(0, payload.length >= 2 ? 2 : 0)));
            setTimeout(() => finish(), 3000);
            buf = Buffer.alloc(0);
            return;
          } else finish();
          return;
        }
        if (op === 1 || op === 2) {
          msgOp = op;
          parts = [payload];
        } else if (op === 0) parts.push(payload);
        else continue;
        if (!fin) continue;
        const data = Buffer.concat(parts);
        parts = [];
        if (msgOp === 1) {
          const cmd = data.toString("utf8");
          if (cmd.startsWith("ADD ")) {
            const p = cmd.trim().split(/\s+/);
            const rep = (BigInt(p[1]) + BigInt(p[2])).toString();
            send(1, Buffer.from(rep, "utf8"));
            println(`${cmd} -> ${rep}`);
          } else if (cmd.startsWith("REV")) {
            const sp = cmd.indexOf(" ");
            const t = sp < 0 ? "" : cmd.slice(sp + 1);
            const rep = Array.from(t).reverse().join("");
            send(1, Buffer.from(rep, "utf8"));
            println(`${cmd} -> ${rep}`);
          } else if (cmd === "SHA1") {
            pendingSha = true;
          } else if (cmd === "BYE") {
            send(8, Buffer.from([0x03, 0xe8]));
            closeSent = true;
          }
        } else if (msgOp === 2 && pendingSha) {
          pendingSha = false;
          const hx = crypto.createHash("sha1").update(data).digest("hex");
          send(1, Buffer.from(hx, "utf8"));
          println(`SHA1 ${data.length} -> ${hx}`);
        }
      }
    };

    sock.on("data", (d: Buffer) => {
      buf = buf.length ? Buffer.concat([buf, d]) : d;
      pump();
    });
    sock.on("error", () => finish());
    sock.on("close", () => finish());
  });
}

async function main() {
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i++) {
    println(`session ${i + 1}`);
    await session(args[i]);
  }
  process.stdout.write(outLines.join(""), () => process.exit(0));
}

main();
