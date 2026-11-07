"""A WaveNet capture, run in numpy, sample-aligned and stateful.

The format is ``.nam``: a small JSON header plus one flat list of floats. The list
is written by walking the model in order, so it can only be unpacked by knowing
the graph, which is:

    for each layer array:
        x = rechannel(x)                       # 1x1, no bias
        for each dilation layer:
            z = conv(x, dilation) + mixer(c)   # conv has bias, mixer does not
            a = tanh(z)
            x = x + layer1x1(a)                # 1x1, bias
            head_acc += a
        head = head_rechannel(head_acc)        # 1x1, bias on the last array
    out = head * head_scale

Conv weights are laid out ``(out_channels, in_channels, kernel)`` as PyTorch writes
them, and every conv is causal, so a block can be run exactly if the last
``(kernel - 1) * dilation`` samples are kept per layer.

This is checked against the reference implementation in a test rather than
trusted: the loader refuses a model whose weights do not add up to the exact
number of floats in the file, and the streaming path is compared sample by
sample against the reference forward pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class NotSupported(Exception):
    """A capture that uses a graph this engine does not implement."""


def _conv1x1(w: np.ndarray, x: np.ndarray, b: np.ndarray | None = None) -> np.ndarray:
    y = w[:, :, 0] @ x
    if b is not None:
        y += b[:, None]
    return y


class _Layer:
    """One dilated convolution inside a layer array."""

    def __init__(self, conv_w, conv_b, mix_w, x1_w, x1_b, dilation: int) -> None:
        self.conv_w = conv_w              # (out, in, k)
        self.conv_b = conv_b
        self.mix_w = mix_w                # (ch, cond, 1)
        self.x1_w = x1_w                  # (ch, ch, 1)
        self.x1_b = x1_b
        self.dilation = int(dilation)
        self.kernel = int(conv_w.shape[2])
        self.history_len = (self.kernel - 1) * self.dilation
        self.history: np.ndarray | None = None

    def reset(self) -> None:
        self.history = None

    def process(self, x: np.ndarray, c: np.ndarray):
        ch, frames = x.shape
        if self.history_len:
            if self.history is None:
                self.history = np.zeros((ch, self.history_len), dtype=np.float32)
            ext = np.concatenate((self.history, x), axis=1)
            base = self.history_len
        else:
            ext = x
            base = 0

        z = None
        for j in range(self.kernel):
            # The taps are stored newest-last, so tap j sits at delay (kernel-1-j).
            # Getting this backwards still sounds like an amp, which is exactly why
            # the reference comparison in the tests exists.
            off = base - (self.kernel - 1 - j) * self.dilation
            term = self.conv_w[:, :, j] @ ext[:, off:off + frames]
            z = term if z is None else z + term
        z += self.conv_b[:, None]
        z += _conv1x1(self.mix_w, c)
        a = np.tanh(z)
        x = x + _conv1x1(self.x1_w, a, self.x1_b)

        if self.history_len:
            self.history = ext[:, -self.history_len:].copy()
        return x, a


class _Array:
    def __init__(self, rechan_w, layers, head_w, head_b) -> None:
        self.rechan_w = rechan_w
        self.layers = layers
        self.head_w = head_w
        self.head_b = head_b

    def reset(self) -> None:
        for layer in self.layers:
            layer.reset()

    def process(self, x: np.ndarray, c: np.ndarray, head_in: np.ndarray | None = None):
        x = _conv1x1(self.rechan_w, x)
        head = head_in
        for layer in self.layers:
            x, a = layer.process(x, c)
            # The head accumulates across arrays before each array's rechannel, and a
            # narrower term broadcasts across the accumulated channels. That is what
            # the reference does, silently, and matching it is the whole game.
            head = a if head is None else head + a
        return x, _conv1x1(self.head_w, head, self.head_b)


class WaveNet:
    """A loaded capture. ``process`` takes and returns one mono block."""

    def __init__(self, arrays, head_scale: float, sample_rate: int | None = None) -> None:
        self.arrays = arrays
        self.head_scale = float(head_scale)
        self.sample_rate = sample_rate
        self._primed = False

    # loading ---------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "WaveNet":
        with open(path, "r", encoding="utf-8") as fp:
            doc = json.load(fp)
        if doc.get("architecture") != "WaveNet":
            raise NotSupported(
                f"only WaveNet captures are supported, this one is {doc.get('architecture')!r}"
            )
        cfg = doc["config"]
        weights = np.asarray(doc["weights"], dtype=np.float32)
        if cfg.get("head") is not None:
            raise NotSupported("captures with a separate output head are not supported")

        arrays = []
        pos = 0

        def take(n: int) -> np.ndarray:
            nonlocal pos
            if pos + n > weights.size:
                raise NotSupported(
                    f"weights ended at {pos} of {weights.size} while reading {n} values"
                )
            out = weights[pos:pos + n]
            pos += n
            return out

        for layer_cfg in cfg["layers"]:
            if layer_cfg.get("gated"):
                raise NotSupported("gated activations are not supported")
            if str(layer_cfg.get("activation", "Tanh")).lower() != "tanh":
                raise NotSupported(f"activation {layer_cfg.get('activation')!r} is not supported")
            for key in ("conv_pre_film", "conv_post_film", "input_mixin_pre_film"):
                if key in layer_cfg:
                    raise NotSupported("FiLM conditioning is not supported")

            in_size = int(layer_cfg["input_size"])
            cond = int(layer_cfg["condition_size"])
            ch = int(layer_cfg["channels"])
            head_out = int(layer_cfg["head_size"])
            kernel = int(layer_cfg["kernel_size"])
            head_bias = bool(layer_cfg.get("head_bias", False))
            dilations = [int(d) for d in layer_cfg["dilations"]]

            rechan = take(ch * in_size * 1).reshape(ch, in_size, 1)

            layers = []
            for d in dilations:
                conv_w = take(ch * ch * kernel).reshape(ch, ch, kernel)
                conv_b = take(ch)
                mix_w = take(ch * cond * 1).reshape(ch, cond, 1)
                x1_w = take(ch * ch).reshape(ch, ch, 1)
                x1_b = take(ch)
                layers.append(_Layer(conv_w, conv_b, mix_w, x1_w, x1_b, d))

            head_w = take(head_out * ch).reshape(head_out, ch, 1)
            head_b = take(head_out) if head_bias else None
            arrays.append(_Array(rechan, layers, head_w, head_b))

        head_scale = float(take(1)[0])
        if pos != weights.size:
            raise NotSupported(
                f"{weights.size - pos} weights left over, this capture does not match the "
                "graph this engine implements"
            )
        return cls(arrays, head_scale, sample_rate=doc.get("sample_rate"))

    # streaming -------------------------------------------------------------
    @property
    def receptive_field(self) -> int:
        total = 1
        for arr in self.arrays:
            for layer in arr.layers:
                total += (layer.kernel - 1) * layer.dilation
        return total

    def reset(self) -> None:
        for arr in self.arrays:
            arr.reset()
        self._primed = False

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, dtype=np.float32).reshape(1, -1)
        c = x
        head = None
        for arr in self.arrays:
            x, head = arr.process(x, c, head)
        y = head[0] * self.head_scale
        self._primed = True
        return np.ascontiguousarray(y, dtype=np.float32)
