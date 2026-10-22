import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.util.*;

public class Main {
    static final SecureRandom RNG = new SecureRandom();
    static PrintStream out;

    public static void main(String[] args) throws Exception {
        out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        int n = 0;
        for (String url : args) {
            n++;
            out.println("session " + n);
            try {
                session(url);
            } catch (Exception e) {
                // connection problem: move on
            }
        }
        out.flush();
        System.exit(0);
    }

    static void session(String url) throws Exception {
        String rest = url.startsWith("ws://") ? url.substring(5) : url;
        int slash = rest.indexOf('/');
        String hostport = slash < 0 ? rest : rest.substring(0, slash);
        String target = slash < 0 ? "/" : rest.substring(slash);
        int colon = hostport.lastIndexOf(':');
        String host = colon < 0 ? hostport : hostport.substring(0, colon);
        int port = colon < 0 ? 80 : Integer.parseInt(hostport.substring(colon + 1));

        try (Socket sock = new Socket(host, port)) {
            sock.setTcpNoDelay(true);
            InputStream in = new BufferedInputStream(sock.getInputStream(), 65536);
            OutputStream os = sock.getOutputStream();
            byte[] kb = new byte[16];
            RNG.nextBytes(kb);
            String key = Base64.getEncoder().encodeToString(kb);
            String req = "GET " + target + " HTTP/1.1\r\n" +
                    "Host: " + hostport + "\r\n" +
                    "Upgrade: websocket\r\n" +
                    "Connection: Upgrade\r\n" +
                    "Sec-WebSocket-Key: " + key + "\r\n" +
                    "Sec-WebSocket-Version: 13\r\n\r\n";
            os.write(req.getBytes(StandardCharsets.US_ASCII));
            os.flush();

            // read header block
            ByteArrayOutputStream hb = new ByteArrayOutputStream();
            int state = 0;
            while (true) {
                int c = in.read();
                if (c < 0) return;
                hb.write(c);
                byte[] b = hb.toByteArray();
                int L = b.length;
                if (L >= 4 && b[L - 4] == '\r' && b[L - 3] == '\n' && b[L - 2] == '\r' && b[L - 1] == '\n') break;
                if (L >= 2 && b[L - 2] == '\n' && b[L - 1] == '\n') break;
            }
            String head = new String(hb.toByteArray(), StandardCharsets.ISO_8859_1);
            String[] lines = head.split("\r?\n");
            String status = lines[0];
            int sp1 = status.indexOf(' ');
            String afterVer = sp1 < 0 ? "" : status.substring(sp1 + 1);
            int sp2 = afterVer.indexOf(' ');
            String code = sp2 < 0 ? afterVer : afterVer.substring(0, sp2);
            String reason = sp2 < 0 ? "" : afterVer.substring(sp2 + 1);
            if (!code.equals("101")) {
                out.println("handshake failed: " + code + " " + reason);
                return;
            }
            String accept = null;
            for (int i = 1; i < lines.length; i++) {
                String l = lines[i];
                int c = l.indexOf(':');
                if (c < 0) continue;
                if (l.substring(0, c).trim().equalsIgnoreCase("Sec-WebSocket-Accept")) {
                    accept = l.substring(c + 1).trim();
                }
            }
            MessageDigest md = MessageDigest.getInstance("SHA-1");
            String expect = Base64.getEncoder().encodeToString(
                    md.digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").getBytes(StandardCharsets.US_ASCII)));
            if (accept == null || !accept.equals(expect)) {
                out.println("handshake failed: bad Sec-WebSocket-Accept");
                return;
            }

            boolean closeSent = false;
            boolean pendingSha = false;
            int msgOp = -1;
            ByteArrayOutputStream msg = new ByteArrayOutputStream();
            while (true) {
                int b0 = in.read();
                if (b0 < 0) return;
                int b1 = readByte(in);
                boolean fin = (b0 & 0x80) != 0;
                int op = b0 & 0x0f;
                long len = b1 & 0x7f;
                if (len == 126) {
                    len = ((long) readByte(in) << 8) | readByte(in);
                } else if (len == 127) {
                    len = 0;
                    for (int i = 0; i < 8; i++) len = (len << 8) | readByte(in);
                }
                byte[] mk = null;
                if ((b1 & 0x80) != 0) {
                    mk = new byte[4];
                    readFully(in, mk);
                }
                byte[] payload = new byte[(int) len];
                readFully(in, payload);
                if (mk != null) for (int i = 0; i < payload.length; i++) payload[i] ^= mk[i & 3];

                if (op == 9) {
                    sendFrame(os, 10, payload);
                    continue;
                }
                if (op == 10) continue;
                if (op == 8) {
                    int ccode = payload.length >= 2 ? ((payload[0] & 0xff) << 8) | (payload[1] & 0xff) : 1005;
                    String r = payload.length > 2 ? new String(payload, 2, payload.length - 2, StandardCharsets.UTF_8) : "";
                    out.println(r.isEmpty() ? "closed " + ccode : "closed " + ccode + " " + r);
                    if (!closeSent) {
                        byte[] cp = payload.length >= 2 ? new byte[]{payload[0], payload[1]} : new byte[0];
                        sendFrame(os, 8, cp);
                    }
                    return;
                }
                if (op == 1 || op == 2) {
                    msgOp = op;
                    msg.reset();
                    msg.write(payload);
                } else if (op == 0) {
                    msg.write(payload);
                } else {
                    continue;
                }
                if (!fin) continue;
                byte[] data = msg.toByteArray();
                msg.reset();
                if (msgOp == 1) {
                    String cmd = new String(data, StandardCharsets.UTF_8);
                    if (cmd.startsWith("ADD ")) {
                        String[] p = cmd.trim().split("\\s+");
                        long s = Long.parseLong(p[1]) + Long.parseLong(p[2]);
                        String rep = Long.toString(s);
                        sendFrame(os, 1, rep.getBytes(StandardCharsets.UTF_8));
                        out.println(cmd + " -> " + rep);
                    } else if (cmd.startsWith("REV")) {
                        int sp = cmd.indexOf(' ');
                        String t = sp < 0 ? "" : cmd.substring(sp + 1);
                        int[] cps = t.codePoints().toArray();
                        StringBuilder sb = new StringBuilder();
                        for (int i = cps.length - 1; i >= 0; i--) sb.appendCodePoint(cps[i]);
                        String rep = sb.toString();
                        sendFrame(os, 1, rep.getBytes(StandardCharsets.UTF_8));
                        out.println(cmd + " -> " + rep);
                    } else if (cmd.equals("SHA1")) {
                        pendingSha = true;
                    } else if (cmd.equals("BYE")) {
                        sendFrame(os, 8, new byte[]{0x03, (byte) 0xE8});
                        closeSent = true;
                    }
                } else if (msgOp == 2) {
                    if (pendingSha) {
                        pendingSha = false;
                        MessageDigest d = MessageDigest.getInstance("SHA-1");
                        byte[] h = d.digest(data);
                        StringBuilder sb = new StringBuilder();
                        for (byte x : h) sb.append(String.format("%02x", x & 0xff));
                        String rep = sb.toString();
                        sendFrame(os, 1, rep.getBytes(StandardCharsets.UTF_8));
                        out.println("SHA1 " + data.length + " -> " + rep);
                    }
                }
            }
        }
    }

    static int readByte(InputStream in) throws IOException {
        int c = in.read();
        if (c < 0) throw new EOFException();
        return c;
    }

    static void readFully(InputStream in, byte[] b) throws IOException {
        int off = 0;
        while (off < b.length) {
            int r = in.read(b, off, b.length - off);
            if (r < 0) throw new EOFException();
            off += r;
        }
    }

    static void sendFrame(OutputStream os, int op, byte[] payload) throws IOException {
        int len = payload.length;
        int hl = 2 + (len <= 125 ? 0 : len <= 65535 ? 2 : 8) + 4;
        byte[] f = new byte[hl + len];
        f[0] = (byte) (0x80 | op);
        int p = 2;
        if (len <= 125) {
            f[1] = (byte) (0x80 | len);
        } else if (len <= 65535) {
            f[1] = (byte) (0x80 | 126);
            f[2] = (byte) (len >> 8);
            f[3] = (byte) len;
            p = 4;
        } else {
            f[1] = (byte) (0x80 | 127);
            long l = len;
            for (int i = 0; i < 8; i++) f[2 + i] = (byte) (l >>> (56 - 8 * i));
            p = 10;
        }
        byte[] mk = new byte[4];
        do { RNG.nextBytes(mk); } while (mk[0] == 0 && mk[1] == 0 && mk[2] == 0 && mk[3] == 0);
        System.arraycopy(mk, 0, f, p, 4);
        p += 4;
        for (int i = 0; i < len; i++) f[p + i] = (byte) (payload[i] ^ mk[i & 3]);
        os.write(f);
        os.flush();
    }
}
