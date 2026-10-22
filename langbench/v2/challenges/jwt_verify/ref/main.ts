import * as fs from "fs";
import * as path from "path";
import * as crypto from "crypto";

const dir = process.argv[2];
const cfg: any = JSON.parse(fs.readFileSync(path.join(dir, "config.json"), "utf8"));
const raw = fs.readFileSync(path.join(dir, "tokens.txt"), "utf8");

function b64dec(s: string): Buffer | null {
  if (s.length === 0 || s.length % 4 === 1) return null;
  if (!/^[A-Za-z0-9_-]+$/.test(s)) return null;
  return Buffer.from(s, "base64url");
}
function isObj(v: any): boolean {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}
function parseObj(b: Buffer): any {
  try {
    const v = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(b));
    return isObj(v) ? v : null;
  } catch {
    return null;
  }
}
const has = (o: any, k: string) => Object.prototype.hasOwnProperty.call(o, k);
const HASH: Record<string, [string, number]> = { HS256: ["sha256", 32], HS384: ["sha384", 48], HS512: ["sha512", 64] };
const keys: any[] = Array.isArray(cfg.keys) ? cfg.keys : [];

function check(tok: string): string {
  const segs = tok.split(".");
  if (segs.length !== 3) return "MALFORMED";
  const dec = segs.map(b64dec);
  if (dec.some((d) => d === null)) return "MALFORMED";
  const hdr = parseObj(dec[0]!);
  if (!hdr) return "MALFORMED";
  const pl = parseObj(dec[1]!);
  if (!pl) return "MALFORMED";
  const alg = has(hdr, "alg") ? hdr.alg : undefined;
  if (typeof alg !== "string" || !has(HASH, alg)) return "UNSUPPORTED_ALG";
  const kid = has(hdr, "kid") ? hdr.kid : undefined;
  if (typeof kid !== "string") return "UNKNOWN_KEY";
  const key = keys.find((k) => isObj(k) && k.kid === kid);
  if (!key) return "UNKNOWN_KEY";
  if (key.alg !== alg) return "ALG_MISMATCH";
  const secret = typeof key.k === "string" ? b64dec(key.k) ?? Buffer.alloc(0) : Buffer.alloc(0);
  const [h, len] = HASH[alg];
  const mac = crypto.createHmac(h, secret).update(segs[0] + "." + segs[1], "latin1").digest();
  if (dec[2]!.length !== len || !mac.equals(dec[2]!)) return "BAD_SIGNATURE";
  const now = Number(cfg.now ?? 0), leeway = Number(cfg.leeway ?? 0);
  if (has(pl, "exp") && typeof pl.exp === "number" && now >= pl.exp + leeway) return "EXPIRED";
  if (has(pl, "nbf") && typeof pl.nbf === "number" && now + leeway < pl.nbf) return "NOT_YET_VALID";
  if (!has(pl, "iss") || typeof pl.iss !== "string" || pl.iss !== cfg.issuer) return "BAD_ISSUER";
  const aud = has(pl, "aud") ? pl.aud : undefined;
  let ok = false;
  if (typeof aud === "string") ok = aud === cfg.audience;
  else if (Array.isArray(aud)) ok = aud.some((a) => typeof a === "string" && a === cfg.audience);
  if (!ok) return "BAD_AUDIENCE";
  const sub = typeof pl.sub === "string" ? pl.sub : "";
  let scopes: string[] = [];
  if (has(pl, "scope") && typeof pl.scope === "string") {
    scopes = Array.from(new Set(pl.scope.split(" ").filter((s: string) => s.length > 0)));
    scopes.sort((a, b) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")));
  }
  return "VALID sub=" + sub + " scope=" + scopes.join(",");
}

const out: string[] = [];
let n = 0, valid = 0, invalid = 0;
for (let line of raw.split("\n")) {
  if (line.endsWith("\r")) line = line.slice(0, -1);
  line = line.replace(/^[ \t]+|[ \t]+$/g, "");
  if (line === "") continue;
  n++;
  const r = check(line);
  if (r.startsWith("VALID")) valid++;
  else invalid++;
  out.push(`${n}: ${r}`);
}
out.push("", `valid=${valid} invalid=${invalid}`);
process.stdout.write(out.join("\n") + "\n");
