"""Low-capacity scorer: ridge regression of net R on a handful of features.

Deliberately tiny.  Effective samples are days, not rows, so capacity is
capped by design (<= 8 features, strong L2).  Judge it on CALIBRATION
(realized vs predicted, by bucket), not on in-sample fit.  Stdlib only.
"""
import math


def _solve(A, b):
    """Gaussian elimination with partial pivoting; A is n x n (list of lists)."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            raise ValueError("singular system")
        M[c], M[p] = M[p], M[c]
        for r in range(c + 1, n):
            f = M[r][c] / M[c][c]
            for k in range(c, n + 1):
                M[r][k] -= f * M[c][k]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (M[i][n] - sum(M[i][k] * x[k] for k in range(i + 1, n))) / M[i][i]
    return x


class Ridge:
    def __init__(self, lam=50.0):
        self.lam = lam
        self.mu = self.sd = self.w = None
        self.b0 = 0.0

    def fit(self, X, y):
        n, d = len(X), len(X[0])
        self.mu = [sum(r[j] for r in X) / n for j in range(d)]
        self.sd = []
        for j in range(d):
            v = sum((r[j] - self.mu[j]) ** 2 for r in X) / max(n - 1, 1)
            self.sd.append(math.sqrt(v) or 1.0)
        Z = [[(r[j] - self.mu[j]) / self.sd[j] for j in range(d)] for r in X]
        self.b0 = sum(y) / n
        yc = [v - self.b0 for v in y]
        A = [[0.0] * d for _ in range(d)]
        bv = [0.0] * d
        for zr, yv in zip(Z, yc):
            for i in range(d):
                bv[i] += zr[i] * yv
                for j in range(i, d):
                    A[i][j] += zr[i] * zr[j]
        for i in range(d):
            for j in range(i):
                A[i][j] = A[j][i]
            A[i][i] += self.lam
        self.w = _solve(A, bv)
        return self

    def predict(self, X):
        out = []
        for r in X:
            s = self.b0
            for j, v in enumerate(r):
                s += self.w[j] * (v - self.mu[j]) / self.sd[j]
            out.append(s)
        return out


def bucket_table(pred, y, k=5):
    """Realized mean of y by predicted-quantile bucket (low -> high)."""
    idx = sorted(range(len(pred)), key=lambda i: pred[i])
    out = []
    for b in range(k):
        lo, hi = b * len(idx) // k, (b + 1) * len(idx) // k
        seg = idx[lo:hi]
        if seg:
            out.append((sum(pred[i] for i in seg) / len(seg),
                        sum(y[i] for i in seg) / len(seg), len(seg)))
    return out
