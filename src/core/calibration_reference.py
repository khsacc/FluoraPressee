"""Reference-line catalogues and pixel-to-line candidate matching.

This module deliberately has no Qt dependency.  The GUI may change which
catalogues are visible without changing the locked assignments, while the
matcher works on the union of all currently active catalogues.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from src.core.calibration import CalibrationCore


@dataclass(frozen=True)
class ReferenceLine:
    line_id: str
    standard_id: str
    species: str
    # Populated for "wavelength_nm"-quantity standards at load time. For
    # "raman_shift_cm1"-quantity standards it starts out None and is only
    # filled in by resolve_standard_for_laser(), since the equivalent
    # wavelength depends on the excitation line in use.
    wavelength_nm: float | None = None
    enabled_for_calibration: bool = True
    relative_intensity: float | None = None
    uncertainty_nm: float | None = None
    raman_shift_cm1: float | None = None
    uncertainty_cm1: float | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class ReferenceStandard:
    standard_id: str
    display_name: str
    lines: tuple[ReferenceLine, ...]
    source_url: str | None = None
    # "wavelength_nm" (atomic emission lamp lines, laser-independent) or
    # "raman_shift_cm1" (a Raman-active material measured against whichever
    # excitation line the instrument uses).
    quantity: str = "wavelength_nm"


@dataclass(frozen=True)
class MatchCandidate:
    """One monotonic wavelength solution and its one-to-one peak assignments."""

    coefficients: tuple[float, float, float]  # c0, c1, c2
    assignments: tuple[tuple[int, str], ...]  # measured peak index, reference line_id
    matched_count: int
    rms_nm: float
    center_error_nm: float | None
    score: float


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def load_reference_standards(directory: str | Path) -> dict[str, ReferenceStandard]:
    """Load every line-catalogue JSON in *directory*.

    Invalid files are skipped with a diagnostic so one locally-added catalogue
    cannot prevent the calibration dialog from opening.
    """

    standards: dict[str, ReferenceStandard] = {}
    path = Path(directory)
    if not path.exists():
        return standards

    for filename in sorted(path.glob("*.json")):
        try:
            data = json.loads(filename.read_text(encoding="utf-8"))
            standard_id = str(data["standard_id"]).strip()
            display_name = str(data.get("display_name") or standard_id).strip()
            source_url = data.get("source_url") or None
            quantity = str(data.get("quantity") or "wavelength_nm").strip()
            if quantity not in ("wavelength_nm", "raman_shift_cm1"):
                raise ValueError(f"Unknown quantity {quantity!r}")
            lines = []
            for index, item in enumerate(data.get("lines", [])):
                common = {
                    "standard_id": standard_id,
                    "species": str(item.get("species") or display_name),
                    "enabled_for_calibration": (
                        item.get("enabled_for_calibration", True) is not False
                    ),
                    "relative_intensity": _optional_float(item.get("relative_intensity")),
                    "source_url": item.get("source_url") or source_url,
                }
                if quantity == "raman_shift_cm1":
                    shift = float(item["raman_shift_cm1"])
                    if not math.isfinite(shift):
                        continue
                    line_id = str(
                        item.get("line_id") or f"{standard_id}:{shift:.4f}:{index}"
                    )
                    lines.append(ReferenceLine(
                        line_id=line_id,
                        wavelength_nm=None,
                        raman_shift_cm1=shift,
                        uncertainty_cm1=_optional_float(item.get("uncertainty_cm1")),
                        **common,
                    ))
                else:
                    wavelength = float(item["wavelength_nm"])
                    if not math.isfinite(wavelength) or wavelength <= 0:
                        continue
                    line_id = str(
                        item.get("line_id")
                        or f"{standard_id}:{wavelength:.8f}:{index}"
                    )
                    lines.append(ReferenceLine(
                        line_id=line_id,
                        wavelength_nm=wavelength,
                        uncertainty_nm=_optional_float(item.get("uncertainty_nm")),
                        **common,
                    ))
            sort_key = (
                (lambda line: line.raman_shift_cm1)
                if quantity == "raman_shift_cm1"
                else (lambda line: line.wavelength_nm)
            )
            lines.sort(key=sort_key)
            if standard_id and lines:
                standards[standard_id] = ReferenceStandard(
                    standard_id=standard_id,
                    display_name=display_name,
                    lines=tuple(lines),
                    source_url=source_url,
                    quantity=quantity,
                )
        except Exception as exc:
            print(f"Error loading reference-line catalogue {filename}: {exc}")
    return standards


def resolve_standard_for_laser(
    standard: ReferenceStandard, laser_wavelength_nm: float
) -> ReferenceStandard:
    """Fill in wavelength_nm for a raman_shift_cm1-quantity standard.

    A Raman-shift-native standard's peaks are laser-independent in cm^-1, but
    everything downstream (matching, overlay, sorting) works in wavelength_nm,
    so the equivalent wavelength for the currently selected excitation line is
    computed here. wavelength_nm-quantity standards are returned unchanged.
    """
    if standard.quantity != "raman_shift_cm1":
        return standard
    resolved_lines = tuple(
        replace(
            line,
            wavelength_nm=CalibrationCore.raman_to_nm(
                line.raman_shift_cm1, laser_wavelength_nm
            ),
        )
        for line in standard.lines
    )
    return replace(standard, lines=resolved_lines)


def _poly_values(coefficients: Sequence[float], pixels: np.ndarray) -> np.ndarray:
    c0, c1, c2 = coefficients
    return c0 + c1 * pixels + c2 * pixels**2


def _nearest_one_to_one(
    mapped_nm: np.ndarray,
    lines: Sequence[ReferenceLine],
    tolerance_nm: float,
) -> list[tuple[int, int, float]]:
    """Greedily select the lowest-residual, one-to-one peak/line matches."""

    possible = []
    wavelengths = np.asarray([line.wavelength_nm for line in lines], dtype=float)
    for peak_index, value in enumerate(mapped_nm):
        insertion = int(np.searchsorted(wavelengths, value))
        for line_index in (insertion - 1, insertion):
            if 0 <= line_index < len(lines):
                residual = abs(float(value - wavelengths[line_index]))
                if residual <= tolerance_nm:
                    possible.append((residual, peak_index, line_index))

    matches = []
    used_peaks: set[int] = set()
    used_lines: set[int] = set()
    for residual, peak_index, line_index in sorted(possible):
        if peak_index in used_peaks or line_index in used_lines:
            continue
        used_peaks.add(peak_index)
        used_lines.add(line_index)
        matches.append((peak_index, line_index, residual))
    matches.sort()
    return matches


def _fit_coefficients(
    pixels: np.ndarray,
    wavelengths: np.ndarray,
    allow_quadratic: bool,
) -> tuple[float, float, float]:
    degree = 2 if allow_quadratic and len(pixels) >= 4 else 1
    fitted = np.polyfit(pixels, wavelengths, degree)
    if degree == 1:
        return float(fitted[1]), float(fitted[0]), 0.0
    return float(fitted[2]), float(fitted[1]), float(fitted[0])


def match_from_seed_axis(
    measured_pixels: Sequence[float],
    reference_lines: Sequence[ReferenceLine],
    wavelength_axis_nm: Sequence[float],
    *,
    expected_slope_sign: int | None = None,
) -> MatchCandidate | None:
    """Build a candidate from a vendor-provided approximate/factory axis."""

    pixels = np.asarray(measured_pixels, dtype=float)
    axis = np.asarray(wavelength_axis_nm, dtype=float)
    lines = sorted(reference_lines, key=lambda line: line.wavelength_nm)
    if expected_slope_sign not in (None, -1, 1):
        raise ValueError("expected_slope_sign must be None, -1, or 1")
    if (
        len(pixels) < 2
        or len(axis) < 2
        or len(lines) < 2
        or not np.all(np.isfinite(axis))
    ):
        return None
    estimated = np.interp(pixels, np.arange(len(axis), dtype=float), axis)
    pixel_step = float(np.median(np.abs(np.diff(axis))))
    tolerance = min(2.0, max(0.08, pixel_step * 6.0))
    matches = _nearest_one_to_one(estimated, lines, tolerance)
    if len(matches) < 2:
        return None
    fit_pixels = np.asarray([pixels[peak] for peak, _, _ in matches])
    fit_wavelengths = np.asarray(
        [lines[line].wavelength_nm for _, line, _ in matches]
    )
    coefficients = _fit_coefficients(
        fit_pixels, fit_wavelengths, allow_quadratic=len(matches) >= 4
    )
    derivative = coefficients[1] + 2.0 * coefficients[2] * np.asarray(
        [np.min(pixels), np.max(pixels)]
    )
    if derivative[0] * derivative[1] <= 0:
        return None
    if (
        expected_slope_sign is not None
        and np.any(derivative * expected_slope_sign <= 0)
    ):
        return None
    fitted = _poly_values(coefficients, fit_pixels)
    rms = float(np.sqrt(np.mean((fitted - fit_wavelengths) ** 2)))
    assignments = tuple(
        (peak, lines[line].line_id) for peak, line, _ in matches
    )
    return MatchCandidate(
        coefficients=coefficients,
        assignments=assignments,
        matched_count=len(matches),
        rms_nm=rms,
        center_error_nm=None,
        score=len(matches) * 100.0 - rms,
    )


def find_match_candidates(
    measured_pixels: Sequence[float],
    reference_lines: Sequence[ReferenceLine],
    *,
    center_wavelength_nm: float | None = None,
    detector_midpoint_px: float | None = None,
    locked_assignments: Mapping[int, str] | None = None,
    max_candidates: int = 5,
    allow_reversed: bool = True,
    expected_slope_sign: int | None = None,
) -> list[MatchCandidate]:
    """Find plausible line assignments without a hard-coded instrument dispersion.

    Two peak/line pairs generate an affine hypothesis.  Every hypothesis is
    scored by the number of one-to-one matches and residuals.  A hardware
    centre wavelength, when available, is only a soft ranking term.

    ``locked_assignments`` maps measured-peak indices to line IDs and acts as a
    constraint; candidates never replace those user-confirmed relationships.

    ``expected_slope_sign`` constrains the wavelength direction on the
    working pixel axis.  Use ``1`` when increasing pixel index should mean
    increasing wavelength (the normal case - this still holds after a
    horizontal flip has already been applied to the data/seed axis upstream,
    since that flip restores the normal orientation), ``-1`` for the opposite,
    or ``None`` when either direction is acceptable.
    """

    pixels = np.asarray(measured_pixels, dtype=float)
    lines = sorted(reference_lines, key=lambda line: line.wavelength_nm)
    if len(pixels) < 2 or len(lines) < 2 or not np.all(np.isfinite(pixels)):
        return []
    if expected_slope_sign not in (None, -1, 1):
        raise ValueError("expected_slope_sign must be None, -1, or 1")

    line_index_by_id = {line.line_id: index for index, line in enumerate(lines)}
    locked = {
        int(peak_index): line_index_by_id[line_id]
        for peak_index, line_id in (locked_assignments or {}).items()
        if 0 <= int(peak_index) < len(pixels) and line_id in line_index_by_id
    }
    midpoint = (
        float(detector_midpoint_px)
        if detector_midpoint_px is not None
        else float((np.min(pixels) + np.max(pixels)) / 2.0)
    )

    peak_pairs: list[tuple[int, int]] = []
    if len(locked) >= 2:
        locked_indices = sorted(locked)
        peak_pairs = [
            (locked_indices[i], locked_indices[j])
            for i in range(len(locked_indices))
            for j in range(i + 1, len(locked_indices))
        ]
    elif len(locked) == 1:
        locked_peak = next(iter(locked))
        peak_pairs = [
            (min(locked_peak, other), max(locked_peak, other))
            for other in range(len(pixels))
            if other != locked_peak
        ]
    else:
        peak_pairs = [
            (i, j)
            for i in range(len(pixels))
            for j in range(i + 1, len(pixels))
        ]

    hypotheses: list[tuple[float, float]] = []
    for peak_a, peak_b in peak_pairs:
        delta_pixel = float(pixels[peak_b] - pixels[peak_a])
        if abs(delta_pixel) < 1e-12:
            continue

        if len(locked) >= 2:
            line_pairs = [(locked[peak_a], locked[peak_b])]
        elif len(locked) == 1:
            locked_peak = next(iter(locked))
            locked_line = locked[locked_peak]
            line_pairs = []
            for other_line in range(len(lines)):
                if other_line == locked_line:
                    continue
                if peak_a == locked_peak:
                    line_pairs.append((locked_line, other_line))
                else:
                    line_pairs.append((other_line, locked_line))
        else:
            line_pairs = [
                (a, b)
                for a in range(len(lines))
                for b in range(a + 1, len(lines))
            ]
            if allow_reversed:
                line_pairs += [(b, a) for a, b in line_pairs]

        for line_a, line_b in line_pairs:
            slope = (
                lines[line_b].wavelength_nm - lines[line_a].wavelength_nm
            ) / delta_pixel
            if not allow_reversed and slope <= 0:
                continue
            if (
                expected_slope_sign is not None
                and slope * expected_slope_sign <= 0
            ):
                continue
            intercept = lines[line_a].wavelength_nm - slope * pixels[peak_a]
            hypotheses.append((float(intercept), float(slope)))

    # Pair enumeration is deliberately broad so no instrument-specific
    # dispersion is required, but scoring every combination is unnecessary.
    # The commanded centre is an effective, vendor-neutral way to retain the
    # most plausible hypotheses.  Without one, sample the hypothesis space
    # uniformly rather than biasing it toward any particular dispersion.
    max_hypotheses = 8000
    if len(hypotheses) > max_hypotheses:
        if center_wavelength_nm is not None and math.isfinite(center_wavelength_nm):
            hypotheses.sort(
                key=lambda hypothesis: abs(
                    hypothesis[0] + hypothesis[1] * midpoint
                    - float(center_wavelength_nm)
                )
            )
            hypotheses = hypotheses[:max_hypotheses]
        else:
            indices = np.linspace(
                0, len(hypotheses) - 1, max_hypotheses, dtype=int
            )
            hypotheses = [hypotheses[index] for index in indices]

    # First rank hypotheses using only their affine residuals.  Polynomial
    # refinement is intentionally deferred: fitting hundreds of thousands of
    # near-duplicate hypotheses made the interactive button unacceptably slow.
    raw_candidates = {}
    for intercept, slope in hypotheses:
        if abs(slope) < 1e-12:
            continue
        mapped = intercept + slope * pixels
        if np.any(mapped <= 0):
            continue

        # A few fitted pixels of tolerance permits mild curvature but prevents a
        # dense catalogue from making every arbitrary affine hypothesis look good.
        tolerance = min(1.0, max(0.04, abs(slope) * 4.0))
        matches = _nearest_one_to_one(mapped, lines, tolerance)
        match_by_peak = {peak: line for peak, line, _ in matches}
        if any(match_by_peak.get(peak) != line for peak, line in locked.items()):
            continue
        if len(matches) < max(2, len(locked)):
            continue

        affine_rms = float(np.sqrt(np.mean([residual**2 for _, _, residual in matches])))
        center_error = None
        center_penalty = 0.0
        if center_wavelength_nm is not None and math.isfinite(center_wavelength_nm):
            predicted_center = intercept + slope * midpoint
            center_error = abs(predicted_center - float(center_wavelength_nm))
            predicted_span = max(
                1.0, abs(slope) * max(1.0, float(np.ptp(pixels)))
            )
            center_penalty = min(100.0, 25.0 * center_error / predicted_span)

        assignment_key = tuple(
            (peak, lines[line].line_id) for peak, line, _ in matches
        )
        score = len(matches) * 100.0 - 20.0 * affine_rms / tolerance - center_penalty
        previous = raw_candidates.get(assignment_key)
        if previous is None or score > previous["score"]:
            raw_candidates[assignment_key] = {
                "matches": matches,
                "score": score,
                "center_error": center_error,
            }

    raw_ranked = sorted(
        raw_candidates.items(),
        key=lambda item: (
            -len(item[1]["matches"]),
            -item[1]["score"],
        ),
    )[:250]
    candidates = []
    for assignment_key, raw in raw_ranked:
        matches = raw["matches"]
        fit_pixels = np.asarray([pixels[peak] for peak, _, _ in matches])
        fit_wavelengths = np.asarray(
            [lines[line].wavelength_nm for _, line, _ in matches]
        )
        coefficients = _fit_coefficients(
            fit_pixels, fit_wavelengths, allow_quadratic=len(matches) >= 4
        )
        derivative = coefficients[1] + 2.0 * coefficients[2] * np.asarray(
            [np.min(pixels), np.max(pixels)]
        )
        if derivative[0] * derivative[1] <= 0:
            continue
        if (
            expected_slope_sign is not None
            and np.any(derivative * expected_slope_sign <= 0)
        ):
            continue
        fitted_nm = _poly_values(coefficients, fit_pixels)
        rms = float(np.sqrt(np.mean((fitted_nm - fit_wavelengths) ** 2)))
        score = raw["score"] - rms
        candidates.append(MatchCandidate(
            coefficients=coefficients,
            assignments=assignment_key,
            matched_count=len(matches),
            rms_nm=rms,
            center_error_nm=raw["center_error"],
            score=score,
        ))

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.matched_count,
            -candidate.score,
            candidate.rms_nm,
        ),
    )[:max_candidates]
