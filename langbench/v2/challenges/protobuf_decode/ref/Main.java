import java.io.*;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    // scalar kinds
    static final int INT32 = 0, INT64 = 1, UINT32 = 2, UINT64 = 3, SINT32 = 4, SINT64 = 5, BOOL = 6, STRING = 7,
            BYTES = 8, FIXED32 = 9, FIXED64 = 10, SFIXED32 = 11, SFIXED64 = 12, MSG = 13, ENUM = 14;
    static final String[] SCALAR_NAMES = {"int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool", "string",
            "bytes", "fixed32", "fixed64", "sfixed32", "sfixed64"};

    static final int SINGLE = 0, OPTIONAL = 1, REPEATED = 2, MAP = 3;

    static class Ty {
        int kind;
        int ref; // msg or enum index
        String name;
    }

    static class Field {
        String json;
        long num;
        Ty ty = new Ty();
        Ty keyTy = new Ty();
        int label;
        int oneof;
    }

    static class MsgDesc {
        String name;
        List<Field> fields = new ArrayList<>();
        Map<Long, Field> byNum = new HashMap<>();
        int oneofCount;
    }

    static class EnumDesc {
        String name;
        Map<Long, String> byNum = new HashMap<>();
    }

    static List<MsgDesc> msgs = new ArrayList<>();
    static List<EnumDesc> enums = new ArrayList<>();
    static int root = -1;

    // ---------- tokenizer ----------
    static List<String> toks = new ArrayList<>();
    static int pos = 0;

    static boolean isWord(char c) {
        return Character.isLetterOrDigit(c) || c == '_' || c == '.' || c == '+' || c == '-';
    }

    static void tokenize(String src) {
        int n = src.length(), i = 0;
        while (i < n) {
            char c = src.charAt(i);
            if (Character.isWhitespace(c)) { i++; continue; }
            if (c == '/' && i + 1 < n && src.charAt(i + 1) == '/') {
                while (i < n && src.charAt(i) != '\n') i++;
                continue;
            }
            if (c == '/' && i + 1 < n && src.charAt(i + 1) == '*') {
                int e = src.indexOf("*/", i + 2);
                i = e < 0 ? n : e + 2;
                continue;
            }
            if (c == '"' || c == '\'') {
                int j = i + 1;
                while (j < n && src.charAt(j) != c) { if (src.charAt(j) == '\\') j++; j++; }
                int e = Math.min(j + 1, n);
                toks.add(src.substring(i, e));
                i = e;
                continue;
            }
            if (isWord(c)) {
                int j = i;
                while (j < n && isWord(src.charAt(j))) j++;
                toks.add(src.substring(i, j));
                i = j;
                continue;
            }
            toks.add(String.valueOf(c));
            i++;
        }
    }

    static String peek() { return pos < toks.size() ? toks.get(pos) : ""; }
    static String next() { return pos < toks.size() ? toks.get(pos++) : ""; }
    static boolean more() { return pos < toks.size(); }

    static void skipStatement() {
        int depth = 0;
        while (more()) {
            String t = next();
            if (t.equals("{") || t.equals("[") || t.equals("(")) depth++;
            else if (t.equals("}") || t.equals("]") || t.equals(")")) depth--;
            else if (t.equals(";") && depth <= 0) return;
        }
    }

    static void skipFieldOptions() {
        if (peek().equals("[")) {
            int depth = 0;
            while (more()) {
                String t = next();
                if (t.equals("[")) depth++;
                else if (t.equals("]")) { depth--; if (depth == 0) break; }
            }
        }
    }

    static long parseInt(String s) {
        boolean neg = false;
        if (s.startsWith("-")) { neg = true; s = s.substring(1); }
        else if (s.startsWith("+")) s = s.substring(1);
        long v;
        try {
            if (s.startsWith("0x") || s.startsWith("0X")) v = Long.parseLong(s.substring(2), 16);
            else if (s.length() > 1 && s.startsWith("0")) v = Long.parseLong(s.substring(1), 8);
            else v = Long.parseLong(s);
        } catch (NumberFormatException e) { v = 0; }
        return neg ? -v : v;
    }

    static String camel(String name) {
        StringBuilder sb = new StringBuilder();
        boolean up = false;
        for (int i = 0; i < name.length(); i++) {
            char ch = name.charAt(i);
            if (ch == '_') { up = true; continue; }
            if (up && ch >= 'a' && ch <= 'z') sb.append((char) (ch - 32));
            else sb.append(ch);
            up = false;
        }
        return sb.toString();
    }

    static void parseEnum() {
        EnumDesc ed = new EnumDesc();
        ed.name = next();
        enums.add(ed);
        next(); // {
        while (more() && !peek().equals("}")) {
            String t = peek();
            if (t.equals(";")) { next(); continue; }
            if (t.equals("option") || t.equals("reserved")) { skipStatement(); continue; }
            String vname = next();
            next(); // =
            long num = parseInt(next());
            skipFieldOptions();
            next(); // ;
            ed.byNum.putIfAbsent(num, vname);
        }
        next();
    }

    static void parseField(MsgDesc md, int oneof) {
        Field f = new Field();
        f.label = SINGLE;
        f.oneof = oneof;
        String t = next();
        if (t.equals("repeated")) { f.label = REPEATED; t = next(); }
        else if (t.equals("optional")) { f.label = OPTIONAL; t = next(); }
        if (t.equals("map") && peek().equals("<")) {
            next();
            f.keyTy.name = next();
            next(); // ,
            t = next();
            next(); // >
            f.label = MAP;
        }
        f.ty.name = t;
        String name = next();
        f.json = camel(name);
        next(); // =
        f.num = parseInt(next());
        skipFieldOptions();
        next(); // ;
        md.fields.add(f);
    }

    static int parseMessage() {
        MsgDesc md = new MsgDesc();
        md.name = next();
        int idx = msgs.size();
        msgs.add(md);
        next(); // {
        while (more() && !peek().equals("}")) {
            String t = peek();
            switch (t) {
                case ";": next(); break;
                case "message": next(); parseMessage(); break;
                case "enum": next(); parseEnum(); break;
                case "option": case "reserved": case "extensions": skipStatement(); break;
                case "oneof": {
                    next(); next();
                    int oi = md.oneofCount++;
                    next(); // {
                    while (more() && !peek().equals("}")) {
                        String u = peek();
                        if (u.equals(";")) { next(); continue; }
                        if (u.equals("option")) { skipStatement(); continue; }
                        parseField(md, oi);
                    }
                    next();
                    break;
                }
                default: parseField(md, -1);
            }
        }
        next();
        md.fields.sort(Comparator.comparingLong(f -> f.num));
        for (Field f : md.fields) md.byNum.put(f.num, f);
        return idx;
    }

    static void parseSchema(String src) {
        tokenize(src);
        while (more()) {
            String t = peek();
            if (t.equals("message")) {
                next();
                int i = parseMessage();
                if (root < 0) root = i;
            } else if (t.equals("enum")) {
                next();
                parseEnum();
            } else if (t.equals(";")) next();
            else skipStatement();
        }
        Map<String, Ty> names = new HashMap<>();
        for (int i = 0; i < msgs.size(); i++) { Ty y = new Ty(); y.kind = MSG; y.ref = i; names.put(msgs.get(i).name, y); }
        for (int i = 0; i < enums.size(); i++) { Ty y = new Ty(); y.kind = ENUM; y.ref = i; names.put(enums.get(i).name, y); }
        for (MsgDesc m : msgs) for (Field f : m.fields) {
            resolve(f.ty, names);
            if (f.label == MAP) resolve(f.keyTy, names);
        }
    }

    static void resolve(Ty t, Map<String, Ty> names) {
        for (int i = 0; i < SCALAR_NAMES.length; i++) if (SCALAR_NAMES[i].equals(t.name)) { t.kind = i; return; }
        Ty y = names.get(t.name);
        if (y != null) { t.kind = y.kind; t.ref = y.ref; } else t.kind = BYTES;
    }

    // ---------- values ----------
    static class MsgVal {
        Map<Long, Object> fields = new HashMap<>(); // Long/byte[]/MsgVal, ArrayList, TreeMap
        long[] oneof;
        MsgVal(int mi) { oneof = new long[msgs.get(mi).oneofCount]; }
    }

    static class Trunc extends RuntimeException { Trunc() { super(null, null, false, false); } }

    static class Reader {
        byte[] buf; int pos, end;
        Reader(byte[] b, int p, int e) { buf = b; pos = p; end = e; }
        long varint() {
            long r = 0; int shift = 0;
            while (true) {
                if (pos >= end) throw new Trunc();
                int b = buf[pos++] & 0xff;
                if (shift < 64) r |= ((long) (b & 0x7f)) << shift;
                shift += 7;
                if ((b & 0x80) == 0) break;
            }
            return r;
        }
        long fixed(int n) {
            if (pos + n > end) throw new Trunc();
            long v = 0;
            for (int i = 0; i < n; i++) v |= ((long) (buf[pos + i] & 0xff)) << (8 * i);
            pos += n;
            return v;
        }
        int[] lenDelim() {
            long l = varint();
            if (l < 0 || l > end - pos) throw new Trunc();
            int s = pos;
            pos += (int) l;
            return new int[]{s, pos};
        }
    }

    static void skipField(Reader r, int wt, long fnum) {
        switch (wt) {
            case 0: r.varint(); break;
            case 1: r.fixed(8); break;
            case 5: r.fixed(4); break;
            case 2: r.lenDelim(); break;
            case 3:
                while (true) {
                    long key = r.varint();
                    int w = (int) (key & 7);
                    long n = key >>> 3;
                    if (w == 4) { if (n != fnum) throw new Trunc(); break; }
                    skipField(r, w, n);
                }
                break;
            case 4: break;
            default: throw new Trunc();
        }
    }

    static int wireOf(Ty t) {
        switch (t.kind) {
            case FIXED32: case SFIXED32: return 5;
            case FIXED64: case SFIXED64: return 1;
            case STRING: case BYTES: case MSG: return 2;
            default: return 0;
        }
    }

    static Object readScalar(Reader r, int wt) {
        switch (wt) {
            case 0: return r.varint();
            case 5: return r.fixed(4);
            case 1: return r.fixed(8);
            default: {
                int[] se = r.lenDelim();
                return Arrays.copyOfRange(r.buf, se[0], se[1]);
            }
        }
    }

    static Object defaultVal(Ty t) {
        if (t.kind == MSG) return new MsgVal(t.ref);
        if (t.kind == STRING || t.kind == BYTES) return new byte[0];
        return 0L;
    }

    // returns BigInteger-free representation: for 64-bit unsigned we use Long.toUnsignedString
    static long convInt(Ty t, long v) {
        switch (t.kind) {
            case INT32: case SFIXED32: case ENUM: return (int) v;
            case UINT32: case FIXED32: return v & 0xffffffffL;
            case SINT32: { int u = (int) v; return (u >>> 1) ^ -(u & 1); }
            case SINT64: return (v >>> 1) ^ -(v & 1);
            case BOOL: return v != 0 ? 1 : 0;
            default: return v;
        }
    }

    static String intStr(Ty t, long v) {
        long c = convInt(t, v);
        if (t.kind == UINT64 || t.kind == FIXED64) return Long.toUnsignedString(c);
        return Long.toString(c);
    }

    static class KeyCmp implements Comparator<Object> {
        public int compare(Object a, Object b) {
            if (a instanceof Long) return Long.compare((Long) a, (Long) b);
            return Arrays.compareUnsigned((byte[]) a, (byte[]) b);
        }
    }

    @SuppressWarnings("unchecked")
    static void decodeInto(int mi, MsgVal msg, byte[] buf, int start, int end) {
        MsgDesc md = msgs.get(mi);
        Reader r = new Reader(buf, start, end);
        while (r.pos < r.end) {
            long key = r.varint();
            int wt = (int) (key & 7);
            long fnum = key >>> 3;
            Field f = md.byNum.get(fnum);
            if (f == null) { skipField(r, wt, fnum); continue; }
            if (f.label == MAP) {
                if (wt != 2) { skipField(r, wt, fnum); continue; }
                int[] se = r.lenDelim();
                Object kv = null, vv = null;
                Reader er = new Reader(buf, se[0], se[1]);
                while (er.pos < er.end) {
                    long k2 = er.varint();
                    int w2 = (int) (k2 & 7);
                    long n2 = k2 >>> 3;
                    Ty t = n2 == 1 ? f.keyTy : n2 == 2 ? f.ty : null;
                    if (t != null && wireOf(t) == w2) {
                        if (t.kind == MSG) {
                            int[] cd = er.lenDelim();
                            MsgVal m = vv instanceof MsgVal ? (MsgVal) vv : new MsgVal(t.ref);
                            decodeInto(t.ref, m, buf, cd[0], cd[1]);
                            vv = m;
                        } else {
                            Object v = readScalar(er, w2);
                            if (n2 == 1) kv = v; else vv = v;
                        }
                    } else skipField(er, w2, n2);
                }
                if (kv == null) kv = defaultVal(f.keyTy);
                if (vv == null) vv = defaultVal(f.ty);
                Object mk = kv instanceof Long ? (Object) convInt(f.keyTy, (Long) kv) : kv;
                TreeMap<Object, Object> tm = (TreeMap<Object, Object>) msg.fields.get(fnum);
                if (tm == null) { tm = new TreeMap<>(new KeyCmp()); msg.fields.put(fnum, tm); }
                tm.put(mk, vv);
                continue;
            }
            int ewt = wireOf(f.ty);
            if (f.label == REPEATED) {
                ArrayList<Object> arr = (ArrayList<Object>) msg.fields.get(fnum);
                if (wt == 2 && ewt != 2) {
                    int[] se = r.lenDelim();
                    if (arr == null) { arr = new ArrayList<>(); msg.fields.put(fnum, arr); }
                    Reader sr = new Reader(buf, se[0], se[1]);
                    while (sr.pos < sr.end) arr.add(readScalar(sr, ewt));
                    continue;
                }
                if (wt != ewt) { skipField(r, wt, fnum); continue; }
                Object val;
                if (f.ty.kind == MSG) {
                    int[] se = r.lenDelim();
                    MsgVal m = new MsgVal(f.ty.ref);
                    decodeInto(f.ty.ref, m, buf, se[0], se[1]);
                    val = m;
                } else val = readScalar(r, wt);
                if (arr == null) { arr = new ArrayList<>(); msg.fields.put(fnum, arr); }
                arr.add(val);
                continue;
            }
            if (wt != ewt) { skipField(r, wt, fnum); continue; }
            if (f.oneof >= 0) {
                long cur = msg.oneof[f.oneof];
                if (cur != 0 && cur != fnum) msg.fields.remove(cur);
                msg.oneof[f.oneof] = fnum;
            }
            if (f.ty.kind == MSG) {
                int[] se = r.lenDelim();
                Object ex = msg.fields.get(fnum);
                MsgVal m = ex instanceof MsgVal ? (MsgVal) ex : new MsgVal(f.ty.ref);
                decodeInto(f.ty.ref, m, buf, se[0], se[1]);
                msg.fields.put(fnum, m);
            } else {
                msg.fields.put(fnum, readScalar(r, wt));
            }
        }
    }

    // ---------- output ----------
    static void esc(ByteArrayOutputStream out, byte[] b) {
        out.write('"');
        for (byte bb : b) {
            int c = bb & 0xff;
            switch (c) {
                case '"': out.write('\\'); out.write('"'); break;
                case '\\': out.write('\\'); out.write('\\'); break;
                case 8: out.write('\\'); out.write('b'); break;
                case 9: out.write('\\'); out.write('t'); break;
                case 10: out.write('\\'); out.write('n'); break;
                case 12: out.write('\\'); out.write('f'); break;
                case 13: out.write('\\'); out.write('r'); break;
                default:
                    if (c < 0x20) writeStr(out, String.format("\\u%04x", c));
                    else out.write(c);
            }
        }
        out.write('"');
    }

    static void writeStr(ByteArrayOutputStream out, String s) {
        byte[] b = s.getBytes(StandardCharsets.UTF_8);
        out.write(b, 0, b.length);
    }

    static boolean isDefault(Ty t, Object v) {
        if (v instanceof byte[]) return ((byte[]) v).length == 0;
        if (v instanceof Long) return convInt(t, (Long) v) == 0;
        return false;
    }

    static void fmtVal(Ty t, Object v, ByteArrayOutputStream out) {
        switch (t.kind) {
            case MSG: fmtMsg(t.ref, (MsgVal) v, out); return;
            case STRING: esc(out, (byte[]) v); return;
            case BYTES: writeStr(out, "\"" + Base64.getEncoder().encodeToString((byte[]) v) + "\""); return;
            case BOOL: writeStr(out, ((Long) v) != 0 ? "true" : "false"); return;
            case INT64: case UINT64: case SINT64: case FIXED64: case SFIXED64:
                writeStr(out, "\"" + intStr(t, (Long) v) + "\""); return;
            case ENUM: {
                long x = convInt(t, (Long) v);
                String name = enums.get(t.ref).byNum.get(x);
                if (name != null) esc(out, name.getBytes(StandardCharsets.UTF_8));
                else writeStr(out, Long.toString(x));
                return;
            }
            default: writeStr(out, intStr(t, (Long) v));
        }
    }

    @SuppressWarnings("unchecked")
    static void fmtMsg(int mi, MsgVal m, ByteArrayOutputStream out) {
        out.write('{');
        boolean first = true;
        for (Field f : msgs.get(mi).fields) {
            Object slot = m.fields.get(f.num);
            if (slot == null) continue;
            boolean skip;
            if (f.label == MAP) skip = ((TreeMap<Object, Object>) slot).isEmpty();
            else if (f.label == REPEATED) skip = ((ArrayList<Object>) slot).isEmpty();
            else skip = f.ty.kind != MSG && f.label != OPTIONAL && f.oneof < 0 && isDefault(f.ty, slot);
            if (skip) continue;
            if (!first) out.write(',');
            first = false;
            esc(out, f.json.getBytes(StandardCharsets.UTF_8));
            out.write(':');
            if (f.label == MAP) {
                out.write('{');
                boolean fst = true;
                for (Map.Entry<Object, Object> e : ((TreeMap<Object, Object>) slot).entrySet()) {
                    if (!fst) out.write(',');
                    fst = false;
                    if (e.getKey() instanceof byte[]) esc(out, (byte[]) e.getKey());
                    else writeStr(out, "\"" + e.getKey() + "\"");
                    out.write(':');
                    fmtVal(f.ty, e.getValue(), out);
                }
                out.write('}');
            } else if (f.label == REPEATED) {
                out.write('[');
                ArrayList<Object> arr = (ArrayList<Object>) slot;
                for (int i = 0; i < arr.size(); i++) {
                    if (i > 0) out.write(',');
                    fmtVal(f.ty, arr.get(i), out);
                }
                out.write(']');
            } else fmtVal(f.ty, slot, out);
        }
        out.write('}');
    }

    public static void main(String[] args) throws IOException {
        String dir = args.length > 0 ? args[0] : ".";
        String src = new String(Files.readAllBytes(Paths.get(dir, "schema.proto")), StandardCharsets.UTF_8);
        byte[] data = Files.readAllBytes(Paths.get(dir, "messages.bin"));
        parseSchema(src);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        Reader r = new Reader(data, 0, data.length);
        while (r.pos < r.end) {
            int[] se;
            try { se = r.lenDelim(); } catch (Trunc t) { break; }
            MsgVal m = new MsgVal(root);
            try { decodeInto(root, m, data, se[0], se[1]); } catch (Trunc t) { }
            fmtMsg(root, m, out);
            out.write('\n');
        }
        System.out.write(out.toByteArray());
        System.out.flush();
    }
}
