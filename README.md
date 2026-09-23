# akuvox-c313-root

Roots the Akuvox C313V3 indoor intercom (SigmaStar SSD202D, Linux 4.9). Gives you
a persistent root SSH login. Tested on firmware 313.30.15.906.

## How it works

Product Mode on these units opens SSH (port 22) and starts `remote_debug_server`,
which dials out to a server address you set in the web UI and accepts a
`PROTOCOL_CMD_SHELL` command — arbitrary commands as root. This script is that
server. It waits for the device to call home, then:

- sets a root password (default `hass1234`)
- drops a `/config/basictest.sh` that (re)starts dropbear and the normal UI on every
  boot
- starts dropbear right away
- checks it actually worked

`/config` is the writable UBI partition, so the changes stick across reboots and
firmware updates. A factory reset wipes them; if that happens just run this again.
Re-running on an already-rooted unit is harmless.

## Setup

    pip install -r requirements.txt

Python 3.8+. You'll need this machine's LAN IP (something the intercom can reach).

## Usage

In the intercom's web admin (Security / Advanced / System-Debug, depending on
firmware), set:

- `Product Mode Active` -> Enabled
- `Remote Debug Server IP Address` -> this machine's IP
- `Remote Debug Server` -> Enabled

Apply. The device now dials this machine on port 9500 every ~20 seconds.

Then run it:

    python3 root_provision.py              # root the next device that calls in, then quit
    python3 root_provision.py --continuous # leave running, root a whole batch

You'll get a line like `ROOTED 192.168.1.50  id=0 pw=1 bt=1 db=1` when it's done.

Log in:

    ssh -o HostKeyAlgorithms=+ssh-rsa root@<device-ip>    # password: hass1234

(The `HostKeyAlgorithms=+ssh-rsa` bit is only needed on newer OpenSSH clients that
disabled ssh-rsa by default. The device's dropbear is fine.)

Once you're in and persistence is set, you can flip Product Mode and Remote Debug
back off in the web UI. SSH keeps working; the device just stops phoning home.

## Custom password

    HASH=$(perl -e 'print crypt("yourpass","Ak")')
    python3 root_provision.py --hash "$HASH"

Traditional DES crypt (8 chars max, 2-char salt), same as what's in `/etc/passwd`.

## Options

- `--continuous` — keep going after the first device
- `--hash <hash>` — root password hash to install
- `--port <n>` — listen port (default 9500)

## Protocol notes

The dial-out uses a 10-byte `R2XK` header (magic + big-endian ver/cmd/len) followed
by an AES-256-CBC body and a 2-byte trailer. Key is the ASCII string
`3555B99F95583555B99F95583555B99F`, IV is 16 zero bytes, PKCS7 padding. The device
sends a registration (cmd 0x1) and a `cloud_cfg` JSON request (cmd 0x12); you answer
the cloud_cfg to keep the socket up, then send cmd 0x5 (`PROTOCOL_CMD_SHELL`) with a
shell script. Output comes back on cmd 0x6.

The provisioning script backs up `/config/etc/passwd`, rewrites the root hash, writes
`/config/basictest.sh` (which `/init.sh` runs as root at boot), starts dropbear, and
echoes a marker the script checks. Nothing in the read-only system/rootfs partitions
is touched.

## What changes on the device

- `/config/etc/passwd` — root password (original kept as `passwd.orig`)
- `/config/basictest.sh` — new; keeps SSH up and launches the stock UI at boot

## Disclaimer

The reverse engineering and this tooling were developed with AI assistance
(Claude Opus).
