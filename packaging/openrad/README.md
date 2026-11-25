# OpenRad build used by Radmin-Linux

The native engine is [OpenRad](https://github.com/gringoestrangeiro/openrad)
(MIT, by gringoestrangeiro and asimplestray). Radmin-Linux ships a build of
upstream **v2.4.5** with the patch in this folder, versioned `2.4.5-rl.1`.

What the patch adds:

* Rename that reaches other members, like official Radmin: operation 52,
  action 10 (only the new name, tag 0x03000304). The server ignores the name
  sent at login, so without this the others kept seeing the old name.
* Reading the server's live name change (record 0x132d in operation 41), so
  names changed by other members update without reconnecting.
* A rename made while the service is offline is kept in
  `alias-pending` and sent on the next connect.
* Version 2.4.5-rl.1 and pt/ru/vi strings for the new messages, plus a test.

The binary is an asset of the Radmin-Linux release named in the third field of
[`OPENRAD_VERSION`](../../OPENRAD_VERSION), with its sha256 in the second field.

## Rebuild it

```bash
git clone https://github.com/gringoestrangeiro/openrad.git
cd openrad && git checkout v2.4.5
git am /path/to/Radmin-Linux/packaging/openrad/openrad-v2.4.5-rl.1.patch
cargo build --release --locked --bin openrad
```

The release binary was built in the official `rust:1.98-slim-bullseye`
container (glibc 2.30) so it runs on Ubuntu 20.04, Debian 11 and newer.
Tarball layout: `openrad-v2.4.5-rl.1-linux-x86_64/{openrad,LICENSE}`.
