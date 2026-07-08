import math

import numpy as np


def euler2mat(ai, aj, ak):
    si, sj, sk = math.sin(ai), math.sin(aj), math.sin(ak)
    ci, cj, ck = math.cos(ai), math.cos(aj), math.cos(ak)
    return np.array(
        [
            [cj * ck, sj * si * ck - ci * sk, sj * ci * ck + si * sk],
            [cj * sk, sj * si * sk + ci * ck, sj * ci * sk - si * ck],
            [-sj, cj * si, cj * ci],
        ],
        dtype=float,
    )


def euler2quat(ai, aj, ak, axes="sxyz"):
    if axes != "sxyz":
        raise NotImplementedError(f"unsupported axes: {axes}")
    ai *= 0.5
    aj *= 0.5
    ak *= 0.5
    si, sj, sk = math.sin(ai), math.sin(aj), math.sin(ak)
    ci, cj, ck = math.cos(ai), math.cos(aj), math.cos(ak)
    return np.array(
        [
            ci * cj * ck + si * sj * sk,
            si * cj * ck - ci * sj * sk,
            ci * sj * ck + si * cj * sk,
            ci * cj * sk - si * sj * ck,
        ],
        dtype=float,
    )


def mat2euler(mat):
    mat = np.asarray(mat, dtype=float)
    cy = math.sqrt(mat[0, 0] * mat[0, 0] + mat[1, 0] * mat[1, 0])
    if cy > 1e-12:
        ax = math.atan2(mat[2, 1], mat[2, 2])
        ay = math.atan2(-mat[2, 0], cy)
        az = math.atan2(mat[1, 0], mat[0, 0])
    else:
        ax = math.atan2(-mat[1, 2], mat[1, 1])
        ay = math.atan2(-mat[2, 0], cy)
        az = 0.0
    return ax, ay, az
