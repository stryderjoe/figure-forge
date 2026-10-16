"""Read every newer-era model's MESH section (python tests/check_nxg.py <extracted game> ...)."""
import collections
import glob
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from forge.formats import nxgmesh  # noqa: E402

for root in sys.argv[1:]:
    res, errs, ex = collections.Counter(), collections.Counter(), {}
    parts = tris = bad = 0
    for p in glob.glob(os.path.join(root, 'chars', '**', '*.ghg'), recursive=True):
        try:
            m = nxgmesh.MeshSection(open(p, 'rb').read())
            for i in range(len(m.parts)):
                pos, nrm, uv, col, t, skin = m.geometry(i)
                parts += 1
                tris += len(t)
                if any(max(x) >= len(pos) for x in t) or any(not all(math.isfinite(v) for v in q) for q in pos):
                    bad += 1
            res['ok'] += 1
        except Exception as e:
            res['fail'] += 1
            k = str(e)[:60]
            errs[k] += 1
            ex.setdefault(k, p)
    print(root, dict(res), f'parts {parts}, triangles {tris}, parts with bad geometry {bad}')
    for k, n in errs.most_common(8):
        print('   ', n, k, ex[k][-60:])
