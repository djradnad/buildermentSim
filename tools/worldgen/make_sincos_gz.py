"""Packs sincos_fast.bin (written by sincos_fast.exe: MSVC /O2 /fp:fast, float pairs cos, sin for k = 0..999999)
into site/wasm/sincos_pc.bin.gz: the cos block then the sin block, each value as the int32 difference of its float
bits from the previous value (wrapping), gzip. 8 MB -> 0.48 MB. worldgen.js decodeSinCos() undoes it.

    python make_sincos_gz.py sincos_fast.bin ../../site/wasm/sincos_pc.bin.gz
"""
import gzip
import sys

import numpy as np

t = np.fromfile(sys.argv[1], dtype='<i4')
assert t.size == 2000000
blocks = []
for x in (t[0::2], t[1::2]):
    d = np.diff(x.astype(np.int64), prepend=0)
    blocks.append(((d + 2**31) % 2**32 - 2**31).astype('<i4'))
open(sys.argv[2], 'wb').write(gzip.compress(np.concatenate(blocks).tobytes(), 9, mtime=0))
