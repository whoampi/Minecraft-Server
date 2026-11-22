#!/usr/bin/env python3
# Helper RCON minimo. Uso: rcon.py <puerto> "<comando>" [host]
import sys, socket, struct
port = int(sys.argv[1]); cmd = sys.argv[2]
host = sys.argv[3] if len(sys.argv) > 3 else "10.10.10.10"
pw = "admin123"
s = socket.create_connection((host, port), timeout=6)
def snd(i, t, b):
    d = struct.pack("<ii", i, t) + b.encode() + b"\x00\x00"
    s.sendall(struct.pack("<i", len(d)) + d)
def rcv():
    ln = struct.unpack("<i", s.recv(4))[0]; x = b""
    while len(x) < ln: x += s.recv(ln - len(x))
    return x[8:-2].decode(errors="ignore")
snd(1, 3, pw); rcv(); snd(2, 2, cmd)
try: print(rcv())
except Exception: print("(payload disparado)")
