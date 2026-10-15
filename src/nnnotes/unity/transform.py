"""Unity transforms as 4x4 matrices: a quaternion's rotation and a local position, rotation and scale (TRS), in
Unity's left-handed space."""

from __future__ import annotations

import numpy as np

__all__ = ["quat_matrix", "trs"]


def quat_matrix(q: dict) -> np.ndarray:
    x, y, z, w = q["x"], q["y"], q["z"], q["w"]
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
            [0, 0, 0, 1],
        ],
        dtype=np.float64,
    )


def trs(pos: dict, rot: dict, scale: dict) -> np.ndarray:
    t = np.eye(4)
    t[:3, 3] = [pos["x"], pos["y"], pos["z"]]
    return t @ quat_matrix(rot) @ np.diag([scale["x"], scale["y"], scale["z"], 1.0])
