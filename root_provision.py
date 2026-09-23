#!/usr/bin/env python3
"""
Akuvox C313V3 one-shot root provisioner.

Prereqs on the target device (via web admin, once):
  1. Security/Advanced -> "Product Mode Active" = Enabled  (opens SSH:22 + starts remote_debug_server)
  2. "Remote Debug Server IP Address" = THIS machine's IP ; "Remote Debug Server" = Enabled
     (makes the device dial out to <this-ip>:9500)
Then run this on THIS machine. It waits for the device to dial in and roots it automatically.

What it does (idempotent, safe to re-run):
  - sets a known root password (default 'hass1234')
  - installs /config/basictest.sh persistence (starts dropbear SSH + normal UI at every boot; on writable UBI)
  - starts dropbear now
  - verifies and reports "DEVICE ROOTED"
"""
import socket, struct, json, time, base64, sys, threading, argparse
from Crypto.Cipher import AES

KEY=b"3555B99F95583555B99F95583555B99F"; IV=b'\0'*16   # cracked RemoteDebug AES key
SHELL_CMD=0x5   # PROTOCOL_CMD_SHELL ; replies come back on cmd 0x6

# ---- default root password + its DES-crypt hash (salt 'Ak') ----
ROOT_HASH="AkllgHq6vLkmE"   # == crypt('hass1234','Ak')

# ---- persistence hook installed on the device (writable UBI; survives reboot+FW update) ----
BASICTEST_SH = """#!/bin/sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin:/app/bin
# --- persistent SSH access (self-healing) ---
/app/scripts/dropbear.sh init >/dev/null 2>&1
( while true; do
    pgrep dropbear >/dev/null 2>&1 || /app/bin/dropbear -p 22 >/dev/null 2>&1
    sleep 30
  done ) &
# --- normal stock UI ---
sh /app/scripts/app.sh &
"""

def dec(b):
    b=b[:len(b)-(len(b)%16)]; return AES.new(KEY,AES.MODE_CBC,IV).decrypt(b)
def enc(pt):
    if isinstance(pt,str): pt=pt.encode()
    pad=16-(len(pt)%16); return AES.new(KEY,AES.MODE_CBC,IV).encrypt(pt+bytes([pad])*pad)
def frame(cmd,body): b=enc(body); return b'R2XK'+struct.pack('>HHH',1,cmd,len(b))+b+b'\x00\x00'
def parse(buf):
    out=[]
    while len(buf)>=12 and buf[:4]==b'R2XK':
        ver,cmd,ln=struct.unpack('>HHH',buf[4:10])
        if len(buf)<10+ln+2: break
        out.append((cmd,dec(buf[10:10+ln]))); buf=buf[10+ln+2:]
    return out,buf
def clean(pt): return pt.rstrip(bytes(range(1,17)))

def build_provision_cmd(root_hash):
    bt_b64=base64.b64encode(BASICTEST_SH.encode()).decode()
    # single shell script run as root on the device
    return (
        '[ -f /config/etc/passwd.orig ] || cp /config/etc/passwd /config/etc/passwd.orig; '
        f'sed -i "s#^root:[^:]*:#root:{root_hash}:#" /config/etc/passwd; '
        f'echo {bt_b64} | base64 -d > /config/basictest.sh; chmod 755 /config/basictest.sh; '
        '/app/scripts/dropbear.sh init >/dev/null 2>&1; /app/scripts/dropbear.sh start >/dev/null 2>&1; '
        'sleep 1; '
        f'echo "AKROOT id=$(id -u) pw=$(grep -c \'^root:{root_hash}\' /config/etc/passwd) '
        'bt=$([ -x /config/basictest.sh ] && echo 1) db=$(pgrep dropbear >/dev/null && echo 1)"'
    )

class Provisioner:
    def __init__(self, root_hash, continuous):
        self.cmd=build_provision_cmd(root_hash)
        self.continuous=continuous
        self.done=threading.Event()
        self.rooted=set()
        self.lock=threading.Lock()

    def handle(self, conn, addr):
        conn.settimeout(30); buf=b""; sent=False; devinfo="?"; out=[]
        try:
            while True:
                data=conn.recv(8192)
                if not data: break
                buf+=data; msgs,buf=parse(buf)
                for cmd,pt in msgs:
                    txt=clean(pt).decode('latin1','replace')
                    if cmd==0x1 and txt.startswith("IP:"):   # registration
                        devinfo=txt.split(';;')[0][:80]
                    if cmd==0x6:
                        out.append(txt)
                        if "AKROOT" in txt:
                            self.report(addr, devinfo, txt)
                            return
                    if b'cloud_cfg' in pt and not sent:
                        conn.sendall(frame(0x12, json.dumps({"action":"get","target":"cloud_cfg","retcode":0,"data":{}})))
                        conn.sendall(frame(SHELL_CMD, self.cmd))
                        sent=True
        except Exception:
            pass
        finally:
            try: conn.close()
            except: pass

    def report(self, addr, devinfo, marker):
        # marker like: AKROOT id=0 pw=1 bt=1 db=1
        ok = ("id=0" in marker and "pw=1" in marker and "bt=1" in marker)
        with self.lock:
            if devinfo in self.rooted: return
            m = marker.strip().replace("AKROOT ", "")
            if ok:
                self.rooted.add(devinfo)
                print(f"\nROOTED {addr[0]}  {m}")
                print(f"  ssh -o HostKeyAlgorithms=+ssh-rsa root@{addr[0]}   (pw: hass1234)\n", flush=True)
            else:
                print(f"\nincomplete {addr[0]}  {m}\n", flush=True)
            if ok and not self.continuous:
                self.done.set()

    def run(self, port=9500):
        s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        s.bind(("0.0.0.0",port)); s.listen(16); s.settimeout(2)
        print(f"[*] Akuvox root provisioner listening on 0.0.0.0:{port}")
        print(f"[*] Waiting for device dial-in (enable Product Mode + point RemoteDebug server at this IP)...")
        print(f"[*] {'continuous mode (Ctrl-C to stop)' if self.continuous else 'will exit after first device is rooted'}\n")
        try:
            while not self.done.is_set():
                try: c,a=s.accept()
                except socket.timeout: continue
                threading.Thread(target=self.handle,args=(c,a),daemon=True).start()
        except KeyboardInterrupt:
            print("\n[*] stopped.")
        s.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser(description="Akuvox C313V3 one-shot root provisioner")
    ap.add_argument("--hash",default=ROOT_HASH,help="DES-crypt root hash to install (default = crypt of 'hass1234')")
    ap.add_argument("--continuous",action="store_true",help="keep provisioning multiple devices (don't exit after first)")
    ap.add_argument("--port",type=int,default=9500)
    a=ap.parse_args()
    Provisioner(a.hash,a.continuous).run(a.port)
