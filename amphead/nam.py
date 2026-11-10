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

Newer captures use the A2 graph: a per-layer activation, a layer 1x1, a
bottleneck, and a causal head filter instead of a 1x1 head. ``SlimmableContainer``
files hold complete WaveNet submodels and a quality dial; this engine runs the
full-width submodel.
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


def _resolve_activation(spec):
    """Resolve an activation once, not on every block.

    A2 captures name their activation per layer, often as a dict. Looking that
    dict up and branching 20 times a block is measurable; a LeakyReLU is also
    cheaper as max(x, slope*x) than as a three-argument where().
    """
    if isinstance(spec, str):
        name = spec.lower()
        if name == "tanh":
            return np.tanh
        if name == "relu":
            return lambda x: np.maximum(x, 0.0)
        raise NotSupported(f"activation {spec!r} is not supported")
    if not isinstance(spec, dict):
        raise NotSupported(f"activation {spec!r} is not supported")
    name = str(spec.get("type", "")).lower()
    if name == "leakyrelu":
        slope = float(spec.get("negative_slope", 0.01))
        return lambda x: np.maximum(x, x * slope)
    if name == "tanh":
        return np.tanh
    if name == "relu":
        return lambda x: np.maximum(x, 0.0)
    raise NotSupported(f"activation {spec!r} is not supported")


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
        # The taps of one layer are a single matrix-vector product once the input
        # windows are laid side by side. Keeping the flattened weights (and the two
        # 1x1 matrices) out of the callback avoids re-indexing them every block.
        self._wflat = np.ascontiguousarray(conv_w.reshape(conv_w.shape[0], -1))
        self._mix = mix_w[:, 0, 0]
        self._x1 = x1_w[:, :, 0]
        self.history_len = (self.kernel - 1) * self.dilation
        self.history: np.ndarray | None = None
        # Reused each block so the callback does not allocate a fresh (history + block)
        # buffer every callback. The shape is fixed for a given layer and block size.
        self._ext: np.ndarray | None = None

    def reset(self) -> None:
        self.history = None

    def prepare(self, frames: int) -> None:
        """Size the reused history+block buffer before the audio thread needs it."""
        if self.history_len:
            self._ext = np.empty((self.conv_w.shape[0], self.history_len + frames),
                                 dtype=np.float32)

    def process(self, x: np.ndarray, c: np.ndarray):
        ch, frames = x.shape
        if self.history_len:
            if self.history is None:
                self.history = np.zeros((ch, self.history_len), dtype=np.float32)
            width = self.history_len + frames
            if self._ext is None or self._ext.shape != (ch, width):
                self._ext = np.empty((ch, width), dtype=np.float32)
            ext = self._ext
            ext[:, :self.history_len] = self.history
            ext[:, self.history_len:] = x
        else:
            ext = x

        if self.kernel == 1:
            z = self._wflat @ x
        else:
            # A strided view puts tap j of every input channel in one row, so the
            # whole convolution is one matmul instead of one per tap. The view is
            # built with ndarray rather than as_strided: as_strided costs more in
            # validation than the matmul saves at these sizes.
            s0, s1 = ext.strides
            win = np.ndarray(
                (ch, self.kernel, frames), dtype=ext.dtype, buffer=ext,
                strides=(s0, s1 * self.dilation, s1),
            )
            z = self._wflat @ win.reshape(ch * self.kernel, frames)
        z += self.conv_b[:, None]
        z += self._mix[:, None] * c
        a = np.tanh(z)
        residual = self._x1 @ a
        residual += self.x1_b[:, None]
        x += residual

        if self.history_len:
            np.copyto(self.history, ext[:, -self.history_len:])
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


class _A2Layer:
    """One layer of the newer NAM WaveNet graph.

    The newer graph moved from one shared tanh and a 1x1 head to a per-layer
    activation, an optional bottleneck, a layer 1x1, and a causal head
    filter. The weights are still stored in graph order.
    """

    def __init__(self, conv_w, conv_b, mix_w, x1_w, x1_b, h1_w, h1_b, activation, dilation: int) -> None:
        self.conv_w = conv_w              # (bottleneck, ch, k)
        self.conv_b = conv_b
        self.mix_w = mix_w                # (bottleneck, cond, 1)
        self.x1_w = x1_w                  # (ch, bottleneck, 1), optional
        self.x1_b = x1_b
        self.h1_w = h1_w                  # (head_out, bottleneck, 1), optional
        self.h1_b = h1_b
        self.activation = activation
        self.dilation = int(dilation)
        self.kernel = int(conv_w.shape[2])
        self._wflat = np.ascontiguousarray(conv_w.reshape(conv_w.shape[0], -1))
        self._mix = mix_w[:, 0, 0]
        self._x1 = x1_w[:, :, 0] if x1_w is not None else None
        self._h1 = h1_w[:, :, 0] if h1_w is not None else None
        self._act = _resolve_activation(activation)
        self.history_len = (self.kernel - 1) * self.dilation
        self.history: np.ndarray | None = None
        self._ext: np.ndarray | None = None

    def reset(self, zeros: bool = True) -> None:
        self.history = None

    def prepare(self, frames: int) -> None:
        if self.history_len:
            self._ext = np.empty((self.conv_w.shape[0], self.history_len + frames),
                                 dtype=np.float32)

    def process(self, x: np.ndarray, c: np.ndarray):
        ch, frames = x.shape
        if self.history_len:
            if self.history is None:
                self.history = np.zeros((ch, self.history_len), dtype=np.float32)
            width = self.history_len + frames
            if self._ext is None or self._ext.shape != (ch, width):
                self._ext = np.empty((ch, width), dtype=np.float32)
            ext = self._ext
            ext[:, :self.history_len] = self.history
            ext[:, self.history_len:] = x
        else:
            ext = x

        if self.kernel == 1:
            z = self._wflat @ x
        else:
            s0, s1 = ext.strides
            win = np.ndarray(
                (ch, self.kernel, frames), dtype=ext.dtype, buffer=ext,
                strides=(s0, s1 * self.dilation, s1),
            )
            z = self._wflat @ win.reshape(ch * self.kernel, frames)
        z += self.conv_b[:, None]
        z += self._mix[:, None] * c
        a = self._act(z)
        if self._x1 is not None:
            residual = self._x1 @ a
            residual += self.x1_b[:, None]
        else:
            residual = a
        if self._h1 is not None:
            head = self._h1 @ a
            head += self.h1_b[:, None]
        else:
            head = a
        x += residual

        if self.history_len:
            np.copyto(self.history, ext[:, -self.history_len:])
        return x, head

    def prime(self, x: np.ndarray):
        """Set history to what an endless stream of ``x`` (one constant frame)
        would leave behind, and return this layer's constant residual and head.
        """
        if self.history_len:
            self.history = np.repeat(x, self.history_len, axis=1).copy()
        z = np.zeros((self.conv_w.shape[0], 1), dtype=np.float32)
        for j in range(self.kernel):
            z += self.conv_w[:, :, j] @ x
        z += self.conv_b[:, None]
        a = self._act(z)
        residual = _conv1x1(self.x1_w, a, self.x1_b) if self.x1_w is not None else a
        head = _conv1x1(self.h1_w, a, self.h1_b) if self.h1_w is not None else a
        return x + residual, head


class WaveNetA2:
    """A newer NAM WaveNet capture, run sample-aligned and stateful."""

    def __init__(self, rechan_w, layers, head_w, head_b, head_scale: float, sample_rate: int | None = None) -> None:
        self.rechan_w = rechan_w
        self.layers = layers
        self.head_w = head_w
        self.head_b = head_b
        self.head_scale = float(head_scale)
        self.sample_rate = sample_rate
        self.head_hist: np.ndarray | None = None
        self._primed = False
        self._headflat = np.ascontiguousarray(head_w.reshape(head_w.shape[0], -1))

    @classmethod
    def from_doc(cls, doc: dict) -> "WaveNetA2":
        cfg = doc["config"]
        weights = np.asarray(doc["weights"], dtype=np.float32)
        if cfg.get("head") is not None:
            raise NotSupported("A2 captures with a separate output head are not supported")
        layer_cfgs = cfg.get("layers")
        if not isinstance(layer_cfgs, list) or len(layer_cfgs) != 1:
            raise NotSupported("A2 captures must have exactly one layer array")
        lc = layer_cfgs[0]

        for key in (
            "conv_pre_film", "conv_post_film", "input_mixin_pre_film",
            "input_mixin_post_film", "activation_pre_film", "activation_post_film",
            "layer1x1_post_film", "head1x1_post_film",
        ):
            spec = lc.get(key)
            if isinstance(spec, dict) and spec.get("active"):
                raise NotSupported("FiLM conditioning is not supported")
        gating = lc.get("gating_mode")
        if isinstance(gating, list):
            if any(str(g).lower() != "none" for g in gating):
                raise NotSupported("gated activations are not supported")
        elif gating not in (None, "none", False):
            raise NotSupported("gated activations are not supported")
        if int(lc.get("groups_input", 1)) != 1 or int(lc.get("groups_input_mixin", 1)) != 1:
            raise NotSupported("grouped convolutions are not supported")
        if lc.get("slimmable") not in (None, False):
            raise NotSupported("per-layer slimming is not supported")

        in_size = int(lc["input_size"])
        cond = int(lc["condition_size"])
        ch = int(lc["channels"])
        bottleneck = int(lc.get("bottleneck", ch))
        if bottleneck != ch:
            # The graph supports it, but none of the captures we have seen use it.
            # Keep the refusal honest instead of silently misreading weights.
            raise NotSupported("A2 captures with a bottleneck different from channels are not supported")
        head_cfg = lc["head"]
        head_out = int(head_cfg["out_channels"])
        head_kernel = int(head_cfg["kernel_size"])
        head_bias = bool(head_cfg.get("bias", False))
        x1_cfg = lc.get("layer1x1") or lc.get("layer_1x1_config") or {}
        h1_cfg = lc.get("head1x1") or lc.get("head_1x1_config") or {}
        x1_active = bool(x1_cfg.get("active"))
        h1_active = bool(h1_cfg.get("active"))
        if int(x1_cfg.get("groups", 1)) != 1 or int(h1_cfg.get("groups", 1)) != 1:
            raise NotSupported("grouped 1x1 convolutions are not supported")
        h1_out = int(h1_cfg.get("out_channels", head_out)) if h1_active else bottleneck

        kernels = [int(k) for k in (lc.get("kernel_sizes") or [lc.get("kernel_size")])]
        dilations = [int(d) for d in lc["dilations"]]
        activations = lc["activation"]
        if len(kernels) == 1 and len(dilations) > 1:
            kernels = kernels * len(dilations)
        if not isinstance(activations, list) or len(activations) != len(dilations):
            raise NotSupported("A2 activation list does not match its dilations")
        if len(kernels) != len(dilations):
            raise NotSupported("A2 kernel list does not match its dilations")

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

        rechan = take(ch * in_size).reshape(ch, in_size, 1)
        layers = []
        for kernel, dilation, activation in zip(kernels, dilations, activations):
            conv_w = take(bottleneck * ch * kernel).reshape(bottleneck, ch, kernel)
            conv_b = take(bottleneck)
            mix_w = take(bottleneck * cond).reshape(bottleneck, cond, 1)
            x1_w = x1_b = h1_w = h1_b = None
            if x1_active:
                x1_w = take(ch * bottleneck).reshape(ch, bottleneck, 1)
                x1_b = take(ch)
            if h1_active:
                h1_w = take(h1_out * bottleneck).reshape(h1_out, bottleneck, 1)
                h1_b = take(h1_out)
            layers.append(
                _A2Layer(conv_w, conv_b, mix_w, x1_w, x1_b, h1_w, h1_b, activation, dilation)
            )

        head_w = take(head_out * h1_out * head_kernel).reshape(head_out, h1_out, head_kernel)
        head_b = take(head_out) if head_bias else None
        head_scale = float(take(1)[0])
        if pos != weights.size:
            raise NotSupported(
                f"{weights.size - pos} weights left over, this A2 capture does not match the "
                "graph this engine implements"
            )
        return cls(rechan, layers, head_w, head_b, head_scale, sample_rate=doc.get("sample_rate"))

    @property
    def receptive_field(self) -> int:
        total = 1 + (self.head_w.shape[2] - 1)
        for layer in self.layers:
            total += (layer.kernel - 1) * layer.dilation
        return total

    def reset(self) -> None:
        # The reference pads the start with zeros and runs the graph, so biases
        # leave every layer at a constant non-zero state before sample zero. Seed
        # those states directly; a transient here would be audible on the first block.
        x = np.zeros((self.layers[0].conv_w.shape[1], 1), dtype=np.float32)
        head = np.zeros((self.head_w.shape[1], 1), dtype=np.float32)
        for layer in self.layers:
            layer.reset()
            x, term = layer.prime(x)
            head += term
        h = self.head_w.shape[2] - 1
        if h:
            self.head_hist = np.repeat(head, h, axis=1)
        else:
            self.head_hist = np.zeros((head.shape[0], 0), dtype=np.float32)
        self._primed = True

    def prime(self, frames: int = 256) -> "WaveNetA2":
        """Build the bias-primed state before the audio callback asks for a block."""
        if not self._primed:
            self.reset()
        for layer in self.layers:
            layer.prepare(frames)
        return self

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self._primed:
            self.prime(np.asarray(block).size)
        raw = np.asarray(block, dtype=np.float32).reshape(1, -1)
        c = raw
        x = _conv1x1(self.rechan_w, raw)
        head = None
        for layer in self.layers:
            x, term = layer.process(x, c)
            head = term if head is None else head + term
        frames = head.shape[1]
        taps = self.head_w.shape[2]
        if taps == 1:
            y = self._headflat @ head
        else:
            all_head = np.concatenate((self.head_hist, head), axis=1)
            s0, s1 = all_head.strides
            win = np.ndarray(
                (self.head_w.shape[1], taps, frames), dtype=all_head.dtype,
                buffer=all_head, strides=(s0, s1, s1),
            )
            y = self._headflat @ win.reshape(self.head_w.shape[1] * taps, frames)
        if self.head_b is not None:
            y += self.head_b[:, None]
        h = self.head_w.shape[2] - 1
        if h:
            self.head_hist = head[:, -h:].copy()
        return np.ascontiguousarray(y[0] * self.head_scale, dtype=np.float32)


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
            return cls.from_doc(json.load(fp))

    @classmethod
    def from_doc(cls, doc: dict) -> "WaveNet":
        architecture = doc.get("architecture")
        if architecture == "SlimmableContainer":
            return cls._from_container(doc)
        if architecture != "WaveNet":
            raise NotSupported(
                "only WaveNet and SlimmableContainer captures are supported, "
                f"this one is {architecture!r}"
            )
        cfg = doc["config"]
        layer_cfgs = cfg.get("layers")
        if (
            isinstance(layer_cfgs, list)
            and layer_cfgs
            and isinstance(layer_cfgs[0], dict)
            and ("kernel_sizes" in layer_cfgs[0] or "head" in layer_cfgs[0])
        ):
            return WaveNetA2.from_doc(doc)
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

    @classmethod
    def _from_container(cls, doc: dict) -> "WaveNet":
        """Run the full-width submodel of a NAM ``SlimmableContainer``.

        The container holds complete models and a width dial, not weights of its
        own. AmpHead has no quality dial, so it takes the largest ``max_value``
        submodel, which is the full model NAM Core falls back to when the dial
        is above the last threshold. Selecting it costs no accuracy and needs no
        retraining.
        """
        cfg = doc.get("config")
        submodels = cfg.get("submodels") if isinstance(cfg, dict) else None
        if not isinstance(submodels, list) or not submodels:
            raise NotSupported("SlimmableContainer has no submodels")

        def max_value(index: int) -> float:
            entry = submodels[index]
            if not isinstance(entry, dict) or not isinstance(entry.get("model"), dict):
                raise NotSupported(f"SlimmableContainer submodel {index} has no model")
            try:
                return float(entry["max_value"])
            except (KeyError, TypeError, ValueError):
                raise NotSupported(
                    f"SlimmableContainer submodel {index} has no numeric max_value"
                ) from None

        full = max(range(len(submodels)), key=lambda i: (max_value(i), i))
        model_doc = submodels[full]["model"]
        model = cls.from_doc(model_doc)
        if model.sample_rate is None and doc.get("sample_rate") is not None:
            model.sample_rate = doc["sample_rate"]
        return model

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

    def prime(self, frames: int = 256) -> "WaveNet":
        """Run the network over zeros so every layer has its real startup state.

        The first live block used to do this work inside the audio callback, and the
        layers before the last one started from zero history even though their own
        biases make the reference's warm state non-zero. Both are load-time problems,
        so they are paid here, off the audio thread and before the capture is published.

        ``frames`` is the live block size: each layer's reused history buffer is sized
        for it here, so the first callback does not allocate one per layer.
        """
        if not self._primed:
            self.reset()
            import numpy as np

            warm = max(1, self.receptive_field)
            self._run_block(np.zeros(warm, dtype=np.float32))
            self._primed = True
        for arr in self.arrays:
            for layer in arr.layers:
                layer.prepare(frames)
        return self

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self._primed:
            self.prime(np.asarray(block).size)
        y = self._run_block(block)
        self._primed = True
        return y

    def _run_block(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, dtype=np.float32).reshape(1, -1)
        c = x
        head = None
        for arr in self.arrays:
            x, head = arr.process(x, c, head)
        y = head[0] * self.head_scale
        return np.ascontiguousarray(y, dtype=np.float32)
