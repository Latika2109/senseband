"""Chunk-continuous artifact noise: a fixed set of sinusoids evaluated at absolute time."""
from __future__ import annotations

import numpy as np


def multisine(freqs: np.ndarray, phases: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Sum of sinusoids, roughly unit amplitude (divided by sqrt of the count)."""
    out = np.zeros_like(t)
    for f, p in zip(freqs, phases, strict=True):
        out += np.sin(2 * np.pi * f * t + p)
    return out / np.sqrt(len(freqs))
