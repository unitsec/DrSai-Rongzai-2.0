"""Numerical operations independent of GSAS-II project state."""

from __future__ import annotations

import itertools
import numpy as np
from scipy.ndimage import gaussian_filter1d, percentile_filter
from scipy.optimize import least_squares
from scipy.signal import find_peaks, peak_widths


def crystal_axis_basis(cell_vectors):
    """Columns are unit a,b,c axes; moments are not fractional coordinates."""
    lattice = np.asarray(cell_vectors, dtype=float)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        raise ValueError("cell_vectors must be finite 3x3 row lattice vectors")
    if np.linalg.det(lattice) <= 1e-10:
        raise ValueError("Lattice must be right handed and nonsingular")
    return (lattice / np.linalg.norm(lattice, axis=1)[:, None]).T


def moment_to_cartesian(moment, cell_vectors):
    return np.asarray(moment, dtype=float) @ crystal_axis_basis(cell_vectors).T


def moment_to_crystalaxis(moment, cell_vectors):
    return np.asarray(moment, dtype=float) @ np.linalg.inv(
        crystal_axis_basis(cell_vectors)
    ).T


def collinear_modes(site_count, cell_vectors, max_candidates=128):
    """Bounded sign patterns, axis/diagonal and generic stabilizer orientations."""
    if not 1 <= site_count <= 12:
        raise ValueError("Collinear enumeration requires 1..12 magnetic positions")
    directions = [
        np.array(v, dtype=float)
        for v in itertools.product((-1, 0, 1), repeat=3)
        if any(v) and next(x for x in v if x) > 0
    ]
    directions.append(np.sqrt([1.0, 2.0, 3.0]))
    count = len(directions) * 2 ** (site_count - 1)
    if count > max_candidates:
        raise ValueError(
            f"Enumeration needs {count} candidates; bound is {max_candidates}. "
            "Increase max_candidates explicitly or select fewer magnetic sites."
        )
    for direction in directions:
        direction /= np.linalg.norm(moment_to_cartesian(direction, cell_vectors))
        for signs in itertools.product((-1, 1), repeat=site_count - 1):
            yield np.array((1, *signs))[:, None] * direction


def _spectrum(d, y, weights):
    arrays = [np.asarray(a, dtype=float) for a in (d, y, weights)]
    if any(a.ndim != 1 for a in arrays) or len({len(a) for a in arrays}) != 1:
        raise ValueError("Spectrum arrays must be equally sized one-dimensional arrays")
    d, y, weights = arrays
    valid = np.isfinite(d) & np.isfinite(y) & np.isfinite(weights) & (weights > 0) & (d > 0)
    d, y, weights = d[valid], y[valid], weights[valid]
    order = np.argsort(d)
    d, y, weights = d[order], y[order], weights[order]
    if len(d) < 30 or np.any(np.diff(d) <= 0):
        raise ValueError("Need at least 30 valid, distinct positive d-spacing bins")
    return d, y, weights


def difference_peaks(high, low, *, max_strain=0.02, significance=5.0,
                     max_peaks=256, background_window=101):
    """Fit high→low nuclear anchors, then detect significant positive residuals.

    Alignment is an affine d mapping (thermal strain + offset). Scale is fitted
    on nuclear anchors with robust loss and positive-residual clipping. Intensities
    are interpolated, not rebinned: appropriate for sampled intensity profiles.
    """
    if not 0 < max_strain <= 0.05 or significance <= 0 or max_peaks < 1:
        raise ValueError("Invalid peak extraction work bounds")
    hd, hy, hw = _spectrum(*high)
    ld, ly, lw = _spectrum(*low)
    span = min(hd[-1], ld[-1]) - max(hd[0], ld[0])
    if span <= 0:
        raise ValueError("High and low spectra have no overlapping d range")
    # Reserve margins so interpolation is never silently extrapolated.
    margin = max_strain * max(hd[-1], ld[-1]) + span * 0.005
    keep = (ld > hd[0] + margin) & (ld < hd[-1] - margin)
    d, y, w = ld[keep], ly[keep], lw[keep]
    if len(d) < 30:
        raise ValueError("Insufficient overlap after alignment margins")
    window = min(int(background_window) | 1, (min(len(hd), len(d)) // 3) | 1)
    if window < 5:
        raise ValueError("Background window too short")
    hsignal = hy - percentile_filter(hy, 20, size=window)
    lsignal = y - percentile_filter(y, 20, size=window)
    h0 = np.interp(d, hd, hsignal)
    anchor = h0 > max(float(np.percentile(h0, 70)), float(np.max(h0)) * 0.02)
    if np.count_nonzero(anchor) < 10 or np.max(h0) <= 0:
        raise ValueError("Too few nuclear intensity anchors for scale/alignment")
    # Sharp TOF peaks make a zero-shift local least-squares start unreliable.
    # Match nuclear maxima first to initialize the affine thermal mapping.
    hp, _ = find_peaks(hsignal, height=np.max(hsignal) * 0.08, distance=5)
    lp, _ = find_peaks(lsignal, height=np.max(lsignal) * 0.04, distance=5)
    pairs = []
    for index in hp:
        if not len(lp):
            break
        nearest = lp[np.argmin(abs(d[lp] - hd[index]))]
        if abs(d[nearest] - hd[index]) < max_strain * hd[index] + span * 0.005:
            pairs.append((hd[index], d[nearest]))
    strain0, shift0 = 0.0, 0.0
    if len(pairs) >= 2:
        pairs = np.array(pairs)
        linear = least_squares(
            lambda q: pairs[:, 0] * (1 + q[0]) + q[1] - pairs[:, 1],
            [0.0, 0.0], bounds=([-max_strain, -span * 0.005],
                                [max_strain, span * 0.005]), loss="soft_l1",
            f_scale=np.median(np.diff(d)) * 2)
        strain0, shift0 = linear.x
    h0 = np.interp((d - shift0) / (1 + strain0), hd, hsignal)
    anchor = h0 > max(float(np.percentile(h0, 70)), float(np.max(h0)) * 0.02)
    scale0 = max(float(np.median(lsignal[anchor] / h0[anchor])), 1e-6)
    active = anchor.copy()
    p = np.array([scale0, strain0, shift0, 0.0])
    bounds = ([scale0 * 0.05, -max_strain, -span * 0.005, -np.max(abs(y))],
              [scale0 * 20, max_strain, span * 0.005, np.max(abs(y))])

    def components(params):
        scale, strain, shift, offset = params
        mapped = (d - shift) / (1 + strain)
        interp = np.interp(mapped, hd, hsignal)
        variance = 1 / w + scale**2 * np.interp(mapped, hd, 1 / hw)
        return lsignal - scale * interp - offset, np.sqrt(variance)

    for _ in range(4):
        fit = least_squares(lambda q: (components(q)[0] / components(q)[1])[active],
                            p, bounds=bounds, loss="soft_l1", max_nfev=150)
        if not fit.success:
            raise RuntimeError(f"Scale/alignment fit failed: {fit.message}")
        p = fit.x
        residual, sigma = components(p)
        active = anchor & (residual / sigma < 3)
        if np.count_nonzero(active) < 10:
            raise ValueError("Magnetic/structural changes contaminate all nuclear anchors")
    residual, sigma = components(p)
    smooth = gaussian_filter1d(residual, 1)
    smooth_sigma = np.sqrt(gaussian_filter1d(sigma**2, 1)) * 0.55
    z = smooth / smooth_sigma
    indices, props = find_peaks(z, height=significance, prominence=significance / 2,
                                distance=3)
    widths, _, left, right = peak_widths(z, indices, rel_height=0.5)
    peaks = []
    for j, index in enumerate(indices):
        l = max(0, int(np.floor(left[j])) - 2)
        r = min(len(d), int(np.ceil(right[j])) + 3)
        peaks.append({
            "d": float(d[index]), "height": float(smooth[index]),
            "significance": float(z[index]), "width_d": float(
                np.interp(right[j], np.arange(len(d)), d)
                - np.interp(left[j], np.arange(len(d)), d)),
            "integrated_difference": float(np.trapz(residual[l:r], d[l:r])),
            "nuclear_overlap": bool(np.interp((d[index] - p[2]) / (1 + p[1]),
                                             hd, hsignal) > np.max(hsignal) * 0.02),
        })
    peaks.sort(key=lambda peak: -peak["significance"])
    if len(peaks) > max_peaks:
        raise ValueError(f"Found {len(peaks)} peaks, exceeding max_peaks={max_peaks}")
    diagnostics = {
        "scale_high_to_low": float(p[0]), "relative_d_strain": float(p[1]),
        "d_offset": float(p[2]), "background_offset": float(p[3]),
        "anchor_count": int(np.count_nonzero(active)), "peaks": peaks,
        "overlap_d": [float(d[0]), float(d[-1])],
        "alignment_at_bound": bool(abs(p[1]) > 0.98 * max_strain),
        "warning": "Difference peaks are magnetic-sensitive, not proof of magnetic origin; "
                   "structural transitions, impurity peaks and thermal ADP changes can contribute.",
    }
    if diagnostics["alignment_at_bound"]:
        raise ValueError("Alignment reached strain bound; increase bound or inspect structural change")
    return diagnostics, {"d": d, "difference": residual, "sigma": sigma,
                         "high_scaled": lsignal - residual, "low_signal": lsignal}
