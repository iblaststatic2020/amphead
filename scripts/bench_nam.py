"""Dev bench only, not part of the plugin.

Compares amphead/nam.py against the upstream PyTorch implementation, so it needs
`pip install torch neural-amp-modeler` in whatever interpreter runs it. Nothing the
plugin ships imports torch or the upstream package; see tests/test_nam.py for the
parity test that runs without either.
"""

import json, sys, time, glob
import numpy as np
import torch
from nam.models import init_from_nam

torch.set_num_threads(4)

def bench(path, seconds=5.0, block=256, sr=48000):
    t0 = time.time()
    with open(path) as f:
        cfg = json.load(f)
    model = init_from_nam(cfg)
    model.eval()
    load_s = time.time() - t0
    n = int(seconds * sr)
    x = (np.random.randn(block) * 0.1).astype(np.float32)
    # warmup
    with torch.no_grad():
        for _ in range(4):
            model.process(x, sr, normalize=False)
    t0 = time.time()
    with torch.no_grad():
        for _ in range(n // block):
            model.process(x, sr, normalize=False)
    dt = time.time() - t0
    return load_s, dt, seconds / dt

if __name__ == "__main__":
    paths = sys.argv[1:]
    for p in paths:
        try:
            ls, dt, rtf = bench(p)
            print(f"{p.split(chr(92))[-1][:45]:45s} load {ls:5.2f}s  {dt:5.2f}s audio  RTF {rtf:6.2f}x")
        except Exception as e:
            print(f"{p.split(chr(92))[-1][:45]:45s} FAILED {type(e).__name__}: {e}")
