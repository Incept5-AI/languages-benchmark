import base64, hashlib, os, socket, sys

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
out = []


def emit(s):
    out.append(s)


class Conn:
    def __init__(self, sock, buf=b""):
        self.sock, self.buf = sock, buf

    def read(self, n):
        while len(self.buf) < n:
            d = self.sock.recv(65536)
            if not d:
                raise EOFError
            self.buf += d
        r, self.buf = self.buf[:n], self.buf[n:]
        return r

    def frame(self):
        b0, b1 = self.read(2)
        n = b1 & 0x7F
        if n == 126:
            n = int.from_bytes(self.read(2), "big")
        elif n == 127:
            n = int.from_bytes(self.read(8), "big")
        if b1 & 0x80:
            k = self.read(4)
            p = bytes(c ^ k[i % 4] for i, c in enumerate(self.read(n)))
        else:
            p = self.read(n)
        return bool(b0 & 0x80), b0 & 0x0F, p

    def send(self, op, payload):
        n = len(payload)
        h = bytes([0x80 | op])
        if n < 126:
            h += bytes([0x80 | n])
        elif n < 65536:
            h += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            h += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        key = os.urandom(4)
        while key == b"\0\0\0\0":
            key = os.urandom(4)
        self.sock.sendall(h + key + bytes(c ^ key[i % 4] for i, c in enumerate(payload)))


def session(url):
    rest = url[len("ws://"):]
    i = rest.find("/")
    hostport, target = (rest, "/") if i < 0 else (rest[:i], rest[i:])
    host, port = hostport.rsplit(":", 1)
    sock = socket.create_connection((host, int(port)))
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {target} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n")
        sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = sock.recv(65536)
            if not d:
                raise EOFError
            buf += d
        j = buf.index(b"\r\n\r\n")
        head, buf = buf[:j].decode("latin-1"), buf[j + 4:]
        lines = head.split("\r\n")
        parts = lines[0].split(" ", 2)
        if parts[1] != "101":
            emit(f"handshake failed: {parts[1]} {parts[2] if len(parts) > 2 else ''}".rstrip())
            return
        hdrs = {}
        for l in lines[1:]:
            k, _, v = l.partition(":")
            hdrs[k.strip().lower()] = v.strip()
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if hdrs.get("sec-websocket-accept") != want:
            emit("handshake failed: bad Sec-WebSocket-Accept")
            return
        c = Conn(sock, buf)
        sent_close = False
        want_binary = False
        msg_op, msg = None, b""
        while True:
            fin, op, p = c.frame()
            if op == 9:
                c.send(10, p)
                continue
            if op == 10:
                continue
            if op == 8:
                code = int.from_bytes(p[:2], "big")
                reason = p[2:].decode("utf-8")
                emit(f"closed {code} {reason}" if reason else f"closed {code}")
                if not sent_close:
                    c.send(8, p[:2])
                return
            if op in (1, 2):
                msg_op, msg = op, p
            else:
                msg += p
            if not fin:
                continue
            if msg_op == 2:
                if want_binary:
                    h = hashlib.sha1(msg).hexdigest()
                    c.send(1, h.encode())
                    emit(f"SHA1 {len(msg)} -> {h}")
                    want_binary = False
                continue
            text = msg.decode("utf-8")
            if text == "SHA1":
                want_binary = True
            elif text == "BYE":
                c.send(8, b"\x03\xe8")
                sent_close = True
            elif text.startswith("ADD "):
                _, a, b = text.split(" ")
                r = str(int(a) + int(b))
                c.send(1, r.encode())
                emit(f"{text} -> {r}")
            elif text.startswith("REV "):
                r = text[4:][::-1]
                c.send(1, r.encode())
                emit(f"{text} -> {r}")
    finally:
        sock.close()


for n, u in enumerate(sys.argv[1:], 1):
    emit(f"session {n}")
    try:
        session(u)
    except EOFError:
        pass
sys.stdout.buffer.write(("\n".join(out) + "\n").encode())
