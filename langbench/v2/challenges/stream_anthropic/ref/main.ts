import * as fs from "fs";
import * as net from "net";

const out: string[] = [];
function emit(s: string) { out.push(s); }
function flush() { process.stdout.write(out.length ? out.join("\n") + "\n" : ""); out.length = 0; }
function esc(s: string): string {
  return s.replace(/[\\\n\r\t]/g, (c) => c === "\\" ? "\\\\" : c === "\n" ? "\\n" : c === "\r" ? "\\r" : "\\t");
}

interface Resp { status: number; body: Buffer; }

function httpPost(baseUrl: string, path: string, headers: [string, string][], body: Buffer): Promise<Resp> {
  const m = /^http:\/\/([^/:]+)(?::(\d+))?(.*)$/.exec(baseUrl)!;
  const host = m[1]; const port = m[2] ? parseInt(m[2]) : 80; const prefix = m[3] || "";
  const hostHeader = m[2] ? `${host}:${port}` : host;
  return new Promise((resolve, reject) => {
    const sock = net.connect(port, host);
    const chunks: Buffer[] = [];
    let done = false;
    const tryParse = (final: boolean) => {
      const all = Buffer.concat(chunks);
      const he = all.indexOf("\r\n\r\n");
      if (he < 0) { if (final) reject(new Error("no headers")); return; }
      const head = all.subarray(0, he).toString("latin1").split("\r\n");
      const status = parseInt(head[0].split(" ")[1]);
      let cl = -1; let chunked = false;
      for (const h of head.slice(1)) {
        const i = h.indexOf(":"); if (i < 0) continue;
        const k = h.slice(0, i).trim().toLowerCase(); const v = h.slice(i + 1).trim();
        if (k === "content-length") cl = parseInt(v);
        if (k === "transfer-encoding" && v.toLowerCase().includes("chunked")) chunked = true;
      }
      const rest = all.subarray(he + 4);
      if (chunked) {
        const parts: Buffer[] = []; let p = 0;
        while (true) {
          const le = rest.indexOf("\r\n", p);
          if (le < 0) { if (final) break; return; }
          let line = rest.subarray(p, le).toString("latin1");
          const semi = line.indexOf(";"); if (semi >= 0) line = line.slice(0, semi);
          const sz = parseInt(line.trim(), 16);
          if (isNaN(sz)) break;
          if (sz === 0) { finish(status, Buffer.concat(parts)); return; }
          if (le + 2 + sz > rest.length) { if (final) { parts.push(rest.subarray(le + 2)); break; } return; }
          parts.push(rest.subarray(le + 2, le + 2 + sz));
          p = le + 2 + sz + 2;
        }
        finish(status, Buffer.concat(parts));
      } else if (cl >= 0) {
        if (rest.length >= cl) finish(status, rest.subarray(0, cl));
        else if (final) finish(status, rest);
      } else if (final) finish(status, rest);
    };
    const finish = (status: number, b: Buffer) => { if (done) return; done = true; sock.destroy(); resolve({ status, body: b }); };
    sock.on("data", (d) => { chunks.push(d); if (!done) tryParse(false); });
    sock.on("end", () => { if (!done) tryParse(true); });
    sock.on("close", () => { if (!done) tryParse(true); });
    sock.on("error", (e) => { if (!done) { done = true; reject(e); } });
    const req = `POST ${prefix}${path} HTTP/1.1\r\nHost: ${hostHeader}\r\nContent-Length: ${body.length}\r\nx-api-key: ${headers[0][1]}\r\nanthropic-version: 2023-06-01\r\ncontent-type: application/json\r\n\r\n`;
    sock.write(Buffer.concat([Buffer.from(req, "latin1"), body]));
  });
}

interface Block { type: string; text: string; signature: string; id: string; name: string; json: string; }
interface Reply { blocks: Block[]; stop: string; }

let usageIn = 0, usageOut = 0, usageCache = 0;

function parseSSE(body: Buffer): Reply {
  const text = body.toString("utf8");
  const lines = text.split("\n");
  const blocks = new Map<number, Block>();
  let stop = "";
  let ev = ""; let data: string[] = [];
  const dispatch = () => {
    if (data.length === 0) { ev = ""; return; }
    let obj: any;
    try { obj = JSON.parse(data.join("\n")); } catch { ev = ""; data = []; return; }
    const t = ev !== "" ? ev : (obj && obj.type) || "";
    ev = ""; data = [];
    if (t === "message_start") {
      const u = obj.message?.usage || {};
      usageIn += u.input_tokens || 0; usageCache += u.cache_read_input_tokens || 0;
    } else if (t === "content_block_start") {
      const cb = obj.content_block;
      blocks.set(obj.index, { type: cb.type, text: "", signature: "", id: cb.id || "", name: cb.name || "", json: "" });
    } else if (t === "content_block_delta") {
      const b = blocks.get(obj.index); if (!b) return;
      const d = obj.delta;
      if (d.type === "thinking_delta") b.text += d.thinking;
      else if (d.type === "signature_delta") b.signature += d.signature;
      else if (d.type === "text_delta") b.text += d.text;
      else if (d.type === "input_json_delta") b.json += d.partial_json;
    } else if (t === "message_delta") {
      if (obj.delta && obj.delta.stop_reason != null) stop = obj.delta.stop_reason;
      usageOut += obj.usage?.output_tokens || 0;
    }
  };
  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];
    if (i === lines.length - 1 && line === "") break;
    if (line.endsWith("\r")) line = line.slice(0, -1);
    if (line === "") { dispatch(); continue; }
    if (line.startsWith(":")) continue;
    const c = line.indexOf(":");
    let field: string, val: string;
    if (c < 0) { field = line; val = ""; } else { field = line.slice(0, c); val = line.slice(c + 1); if (val.startsWith(" ")) val = val.slice(1); }
    if (field === "event") ev = val;
    else if (field === "data") data.push(val);
  }
  dispatch();
  const idx = [...blocks.keys()].sort((a, b) => a - b);
  return { blocks: idx.map((k) => blocks.get(k)!), stop };
}

function printReply(r: Reply) {
  for (const b of r.blocks) {
    if (b.type === "thinking") { emit("thinking " + esc(b.text)); emit("signature " + esc(b.signature)); }
    else if (b.type === "text") emit("text " + esc(b.text));
    else if (b.type === "tool_use") emit(`tool_use ${esc(b.id)} ${esc(b.name)} ${esc(b.json)}`);
  }
  emit("stop_reason " + esc(r.stop));
}

function runTool(name: string, input: any): [boolean, string] {
  if (name === "measure") {
    const text: string = String(input.text ?? ""); const unit = input.unit;
    if (unit === "code_points") return [true, String([...text].length)];
    if (unit === "utf8_bytes") return [true, String(Buffer.byteLength(text, "utf8"))];
    if (unit === "utf16_units") return [true, String(text.length)];
    return [false, "unsupported unit: " + unit];
  }
  if (name === "reverse") return [true, [...String(input.text ?? "")].reverse().join("")];
  return [false, "unknown tool: " + name];
}

async function main() {
  const [dataDir, baseUrl] = process.argv.slice(2);
  const req = JSON.parse(fs.readFileSync(dataDir + "/request.json", "utf8"));
  const userMsg = { role: "user", content: req.prompt };
  const mk = (messages: any[]) => Buffer.from(JSON.stringify({
    model: req.model, max_tokens: req.max_tokens, stream: true,
    thinking: { type: "enabled", budget_tokens: req.thinking_budget },
    system: req.system, tools: req.tools, messages,
  }), "utf8");
  const send = async (messages: any[]): Promise<Reply | null> => {
    const r = await httpPost(baseUrl, "/v1/messages", [["x-api-key", req.api_key]], mk(messages));
    if (r.status !== 200) {
      let et = "", em = "";
      try { const o = JSON.parse(r.body.toString("utf8")); et = o.error.type; em = o.error.message; } catch {}
      emit(`ERROR ${r.status} ${esc(String(et))}: ${esc(String(em))}`);
      return null;
    }
    return parseSSE(r.body);
  };
  emit("request 1");
  const r1 = await send([userMsg]);
  if (!r1) { flush(); return; }
  printReply(r1);
  const asst: any[] = []; const results: any[] = [];
  for (const b of r1.blocks) {
    if (b.type === "thinking") asst.push({ type: "thinking", thinking: b.text, signature: b.signature });
    else if (b.type === "text") asst.push({ type: "text", text: b.text });
    else if (b.type === "tool_use") {
      const input = b.json.trim() === "" ? {} : JSON.parse(b.json);
      asst.push({ type: "tool_use", id: b.id, name: b.name, input });
      const [ok, res] = runTool(b.name, input);
      emit(`${ok ? "result" : "error"} ${esc(b.id)} ${esc(res)}`);
      const tr: any = { type: "tool_result", tool_use_id: b.id, content: res };
      if (!ok) tr.is_error = true;
      results.push(tr);
    }
  }
  emit("request 2");
  const r2 = await send([userMsg, { role: "assistant", content: asst }, { role: "user", content: results }]);
  if (!r2) { flush(); return; }
  printReply(r2);
  emit(`usage input=${usageIn} output=${usageOut} cache_read=${usageCache}`);
  flush();
}

main().then(() => process.exit(0), (e) => { flush(); process.stderr.write(String(e) + "\n"); process.exit(0); });
