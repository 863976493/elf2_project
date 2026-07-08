import numpy as np


def compose(t, r, z):
    out = np.eye(4, dtype=float)
    out[:3, :3] = np.asarray(r, dtype=float) @ np.diag(np.asarray(z, dtype=float))
    out[:3, 3] = np.squeeze(np.asarray(t, dtype=float))
    return out


def decompose(mat):
    mat = np.asarray(mat, dtype=float)
    t = mat[:3, 3].copy()
    rz = mat[:3, :3].copy()
    z = np.linalg.norm(rz, axis=0)
    z[z == 0.0] = 1.0
    r = rz / z
    return t, r, z, np.zeros(3, dtype=float)
