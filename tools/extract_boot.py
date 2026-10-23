"""Copy the game's own `boot` script out of your Helldivers 2 install.

The loader replaces `boot` and runs the original first, so a build needs the
original bytes. They are the game's, not ours, and are not in this repository:
this reads them from the game's data bundles (bundles.nxa, DSAR / LZ4) and
writes boot.luac next to build.py.

    pip install lz4
    python tools/extract_boot.py "<...>/steamapps/common/Helldivers 2"
"""
import bisect
import os
import struct
import sys

import lz4.block

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from build import murmur64  # noqa: E402

LUA_TYPE = murmur64(b"lua")
BOOT = murmur64(b"boot")


class DSAR:
    """A DSAR container: chunks, each LZ4 or stored, read lazily."""

    def __init__(self, path):
        self.f = open(path, "rb")
        head = self.f.read(0x20)
        if head[:4] != b"DSAR":
            raise SystemExit("%s is not a DSAR file" % path)
        self.n = struct.unpack_from("<I", head, 8)[0]
        self.total = struct.unpack_from("<Q", head, 16)[0]
        self.f.seek(0x20)
        raw = self.f.read(32 * self.n)
        self.rows = [struct.unpack_from("<QQII", raw, i * 32) for i in range(self.n)]
        self.starts = [r[0] for r in self.rows]
        self.cache = {}

    def chunk(self, i):
        if i not in self.cache:
            d_off, c_off, d_size, c_size = self.rows[i]
            self.f.seek(c_off)
            blob = self.f.read(c_size)
            data = blob if c_size == d_size else lz4.block.decompress(blob, uncompressed_size=d_size)
            if len(self.cache) > 64:
                self.cache.clear()
            self.cache[i] = data
        return self.cache[i]

    def read(self, off, size):
        i = bisect.bisect_right(self.starts, off) - 1
        out = bytearray()
        while size > 0 and 0 <= i < self.n:
            c = self.chunk(i)
            start = off - self.starts[i]
            take = min(size, len(c) - start)
            if take <= 0:
                break
            out += c[start:start + take]
            off += take
            size -= take
            i += 1
        return bytes(out)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    data = os.path.join(sys.argv[1], "data")
    index = DSAR(os.path.join(data, "bundles.nxa"))
    d = index.read(0, index.total)
    if d[:4] != b"DSAA":
        raise SystemExit("bundles.nxa: not the expected index")
    _, _, _, n_bundles, n_entries = struct.unpack_from("<HHIII", d, 4)

    def cstr(o):
        return d[o:d.index(b"\0", o)].decode("utf-8", "replace")

    bundles = [cstr(struct.unpack_from("<I", d, 0x18 + n_entries * 24 + i * 4)[0]) for i in range(n_bundles)]
    opened = {}
    for e in range(n_entries):
        size, _name, n_chunks, chunk_table = struct.unpack_from("<QIIQ", d, 0x18 + e * 24)
        pieces = [struct.unpack_from("<QQ", d, chunk_table + k * 16) for k in range(n_chunks)]

        def read(off, n):
            out = bytearray()
            for k, (file_off, loc) in enumerate(pieces):
                end = pieces[k + 1][0] if k + 1 < n_chunks else size
                if end <= off or file_off >= off + n:
                    continue
                b = loc >> 56
                if b not in opened:
                    opened[b] = DSAR(os.path.join(data, bundles[b]))
                s0, s1 = max(0, off - file_off), min(end - file_off, off + n - file_off)
                out += opened[b].read((loc & ((1 << 56) - 1)) + s0, s1 - s0)
            return bytes(out)

        if size < 0x48:
            continue
        magic, n_types, n_files = struct.unpack("<III", read(0, 12))
        if magic != 0xF0000011:
            continue
        rows = read(0x48 + n_types * 32, n_files * 80)
        for i in range(n_files):
            fid, tid, off = struct.unpack_from("<3Q", rows, i * 80)
            if fid == BOOT and tid == LUA_TYPE:
                toc_size = struct.unpack_from("<I", rows, i * 80 + 56)[0]
                body = read(off, toc_size)
                length = struct.unpack_from("<I", body, 0)[0]
                code = body[8:8 + length]
                if code[:5] != b"\x1bLuaQ":
                    raise SystemExit("the game's boot is not Lua 5.1 bytecode: %r" % code[:12])
                out = os.path.join(HERE, "boot.luac")
                open(out, "wb").write(code)
                print("wrote %s (%d bytes)" % (out, len(code)))
                return
    raise SystemExit("no `boot` script in %s" % data)


if __name__ == "__main__":
    main()
