"""Frozen AeroTransformer-L evaluator for the prospective SuperWing benchmark.

The released checkpoint predicts structured surface fields, not scalar CL/CD.
This module owns the deterministic bridge from the repository's physical 38-D
geometry to the checkpoint grid, integrates the predicted fields, and trims AoA
to the benchmark-owned lift target.

Heavy dependencies are imported only when ``AeroTransformerEvaluator`` is built.
Geometry construction itself needs only NumPy and SciPy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from math import comb
from pathlib import Path
import sys
from typing import Sequence

import numpy as np
from scipy.interpolate import CubicSpline


DESIGN_DIM = 38
POINT_GRID_SHAPE = (3, 129, 257)
CELL_GRID_SHAPE = (3, 128, 256)
MODEL_CONFIG_SHA256 = "a6cedb0a3dba73f99c41bcfdf3913d37a912c6b0558d5f906e6633aebdca8ef2"
MODEL_WEIGHTS_SHA256 = "4fa8fb4e3986cdb7a4a24f1f74410c54412beb5191857c76fdd4c4f0c5dc8bfa"
DEFAULT_MACH_GRID = (0.7853, 0.8195, 0.8530)
DEFAULT_CL_TARGET = 0.41
DEFAULT_AOA_SEED_DEG = 6.6401110468


@dataclass(frozen=True)
class FixedCLResult:
    """Vectorized fixed-lift prediction for ``N`` wings and ``G`` Machs."""

    cl: np.ndarray
    cd: np.ndarray
    ld: np.ndarray
    aoa_deg: np.ndarray
    converged: np.ndarray
    score: np.ndarray


def _validated_geometry(geometry_38d: Sequence[float]) -> np.ndarray:
    geom = np.asarray(geometry_38d, dtype=np.float64).reshape(-1)
    if geom.shape != (DESIGN_DIM,) or not np.all(np.isfinite(geom)):
        raise ValueError("geometry must contain exactly 38 finite values")
    return geom


def reference_area(geometry_38d: Sequence[float]) -> float:
    """Return the dataset-authoritative exposed half-wing reference area.

    The cabin/root segment is excluded, matching the SuperWing mesh generator and
    ``index.npy``. Unlike the simple trapezoid expression, this includes the
    physical root-adjustment convention.
    """

    g = _validated_geometry(geometry_38d)
    sweep, aspect_ratio, taper, kink_fraction, rootadj = g[0], g[3], g[4], g[5], g[6]
    half_span = 0.25 * aspect_ratio * (1.0 + taper)
    cabin = 0.1 * half_span
    kink = kink_fraction * half_span
    x_le_kink = np.tan(np.deg2rad(sweep)) * kink
    chord_kink = taper * kink_fraction + 1.0 - kink_fraction
    root_adjustment_length = rootadj * (x_le_kink + chord_kink - 1.0)
    chord_cabin = (
        0.1 * taper
        + 0.9
        + root_adjustment_length * (kink_fraction - 0.1) / kink_fraction
    )
    area = (
        0.5 * (chord_cabin + chord_kink) * (kink - cabin)
        + 0.5 * (chord_kink + taper) * (half_span - kink)
    )
    if not np.isfinite(area) or area <= 0.0:
        raise ValueError(f"geometry produces an invalid reference area: {area!r}")
    return float(area)


def _clustered_chord(nn: int = 128, *, a0: float = 0.0079, a1: float = 0.96) -> np.ndarray:
    yt = np.linspace(0.0, 1.0, nn)
    aa = (1.0 - np.cos(a0 * np.pi)) / 2.0
    dd = (1.0 - np.cos(a1 * np.pi)) / 2.0 - aa
    angle = np.pi * (a0 * (1.0 - yt) + a1 * yt)
    return ((1.0 - np.cos(angle)) / 2.0 - aa) / dd


def _cst_curve(x: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
    order = len(coefficients) - 1
    shape = np.zeros_like(x)
    for index, coefficient in enumerate(coefficients):
        shape += (
            coefficient
            * comb(order, index)
            * np.power(x, index)
            * np.power(1.0 - x, order - index)
        )
    curve = np.sqrt(x) * (1.0 - x) * shape
    curve[0] = 0.0
    curve[-1] = 0.0
    return curve


def _base_airfoil(g: np.ndarray, *, tail: float = 0.008) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = _clustered_chord(128)
    upper = _cst_curve(x, g[17:27])
    lower = _cst_curve(x, g[27:37])
    thickness = upper - lower
    max_index = int(np.argmax(thickness))
    scale = (g[37] - tail * x[max_index]) / thickness[max_index]
    upper = upper * scale + 0.5 * tail * x
    lower = lower * scale - 0.5 * tail * x
    return x, upper, lower


def geometry_to_point_grid(geometry_38d: Sequence[float]) -> np.ndarray:
    """Construct the physical ``3 x 129 x 257`` AeroTransformer point grid.

    Channels are streamwise x, vertical z, and semispan y. The chordwise seam is
    lower trailing edge -> leading edge -> upper trailing edge -> duplicated lower
    trailing edge, matching the released structured tensor topology.
    """

    g = _validated_geometry(geometry_38d)
    sweep, dihedral_tip, dihedral_kink = g[0:3]
    aspect_ratio, taper, kink_fraction, rootadj = g[3:7]
    rtcs, cambers, twists = g[7:10], g[10:13], g[13:17]

    half_span = 0.25 * aspect_ratio * (1.0 + taper)
    cabin = 0.1 * half_span
    kink = kink_fraction * half_span
    tip = half_span + 1e-9
    y_le = np.linspace(cabin, half_span, 129)
    x_le = np.tan(np.deg2rad(sweep)) * y_le
    x_le_kink = np.tan(np.deg2rad(sweep)) * kink

    kink_index = int(np.floor((kink_fraction - 0.1) / 0.9 * 129))
    y_inner = y_le[: kink_index + 1]
    y_outer = y_le[kink_index + 1 :]
    z_inner = np.tan(np.deg2rad(dihedral_kink)) * y_inner
    z_kink = np.tan(np.deg2rad(dihedral_kink)) * kink
    z_tip = np.tan(np.deg2rad(dihedral_tip)) * half_span
    z_outer_spline = CubicSpline(
        [kink, tip],
        [z_kink, z_tip],
        bc_type=((1, np.tan(np.deg2rad(dihedral_kink))), "not-a-knot"),
        extrapolate=False,
    )
    z_le = np.concatenate((z_inner, z_outer_spline(y_outer)))

    mid_inner = 0.5 * kink
    mid_outer = 0.5 * (kink + half_span)
    x_airfoil, upper, lower = _base_airfoil(g)
    camber = 0.5 * (upper + lower)
    thickness = upper - lower
    thickness_ratio = CubicSpline(
        [0.0, kink, mid_outer, tip],
        [1.0, rtcs[0], rtcs[0] * rtcs[1], np.prod(rtcs)],
        extrapolate=False,
    )(y_le)
    camber_ratio = CubicSpline(
        [0.0, mid_inner, kink, mid_outer, tip],
        [0.0, cambers[0] * cambers[1], cambers[1], 1.0, cambers[2]],
        extrapolate=False,
    )(y_le)
    upper_sections = np.outer(camber, camber_ratio) + 0.5 * np.outer(thickness, thickness_ratio)
    lower_sections = np.outer(camber, camber_ratio) - 0.5 * np.outer(thickness, thickness_ratio)

    chord_kink = taper * kink_fraction + 1.0 - kink_fraction
    root_adjustment_length = rootadj * (x_le_kink + chord_kink - 1.0)
    chord_cabin = (
        0.1 * taper
        + 0.9
        + root_adjustment_length * (kink_fraction - 0.1) / kink_fraction
    )
    chords = np.where(
        y_le < kink,
        chord_cabin + (chord_kink - chord_cabin) * (y_le - cabin) / (kink - cabin),
        chord_kink + (taper - chord_kink) * (y_le - kink) / (half_span - kink),
    )

    cumulative_twists = [0.0] + [float(np.sum(twists[: index + 1])) for index in range(4)]
    twist_deg = CubicSpline(
        [0.0, mid_inner, kink, mid_outer, tip], cumulative_twists, extrapolate=True
    )(y_le)

    # SuperWing uses 128 points on each CST curve plus three explicit trailing-
    # edge points. This yields 127 lower, 127 upper, and 3 tail vertices = 257.
    x_closed = np.concatenate((np.flip(x_airfoil[1:]), x_airfoil[:-1], np.ones(3)))
    z_closed = np.concatenate(
        (
            np.flip(lower_sections[1:], axis=0),
            upper_sections[:-1],
            np.linspace(upper_sections[-1], lower_sections[-1], 3),
        ),
        axis=0,
    )
    twist_rad = np.deg2rad(twist_deg)
    x_rot = (
        np.cos(twist_rad)[None, :] * x_closed[:, None]
        + np.sin(twist_rad)[None, :] * z_closed
    )
    z_rot = (
        -np.sin(twist_rad)[None, :] * x_closed[:, None]
        + np.cos(twist_rad)[None, :] * z_closed
    )
    x_physical = x_rot * chords[None, :] + x_le[None, :]
    z_physical = z_rot * chords[None, :] + z_le[None, :]
    span = np.repeat(y_le[None, :], 257, axis=0)
    grid = np.stack((x_physical, z_physical, span), axis=0).transpose(0, 2, 1)
    if grid.shape != POINT_GRID_SHAPE or not np.all(np.isfinite(grid)):
        raise ValueError(f"invalid AeroTransformer point grid: {grid.shape}")
    return np.ascontiguousarray(grid, dtype=np.float64)


def point_grid_to_cell_grid(point_grid: np.ndarray) -> np.ndarray:
    points = np.asarray(point_grid, dtype=np.float64)
    if points.shape != POINT_GRID_SHAPE or not np.all(np.isfinite(points)):
        raise ValueError(f"point grid must have shape {POINT_GRID_SHAPE}")
    cells = 0.25 * (
        points[:, 1:, 1:]
        + points[:, 1:, :-1]
        + points[:, :-1, 1:]
        + points[:, :-1, :-1]
    )
    return np.ascontiguousarray(cells, dtype=np.float64)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AeroTransformerEvaluator:
    """Pinned `ATsurf_L` inference and deterministic fixed-CL scoring."""

    def __init__(
        self,
        *,
        model_dir: str | Path,
        flogen_root: str | Path | None = None,
        device: str = "cpu",
        inference_chunk_size: int = 16,
        torch_intraop_threads: int = 1,
        torch_interop_threads: int = 1,
    ) -> None:
        if flogen_root is not None:
            root = str(Path(flogen_root).resolve())
            if root not in sys.path:
                sys.path.insert(0, root)
        try:
            import torch
            from flowvae.ml_operator.config import ModelConfig
            from flowvae.ml_operator.operator import load_model_weights
            from flowvae.post import intergal_output
        except ImportError as exc:  # pragma: no cover - depends on optional runtime
            raise RuntimeError("AeroTransformer requires pinned torch and floGen dependencies") from exc

        model_dir = Path(model_dir)
        config_path = model_dir / "model_config"
        weights_path = model_dir / "best_model_weights"
        if _file_sha256(config_path) != MODEL_CONFIG_SHA256:
            raise ValueError("AeroTransformer model_config SHA-256 mismatch")
        if _file_sha256(weights_path) != MODEL_WEIGHTS_SHA256:
            raise ValueError("AeroTransformer weights SHA-256 mismatch")
        if device == "mps" and not torch.backends.mps.is_available():
            raise ValueError("requested MPS device is unavailable")
        if int(torch_intraop_threads) <= 0 or int(torch_interop_threads) <= 0:
            raise ValueError("torch thread counts must be positive")
        if torch.get_num_threads() != int(torch_intraop_threads):
            torch.set_num_threads(int(torch_intraop_threads))
        if torch.get_num_interop_threads() != int(torch_interop_threads):
            torch.set_num_interop_threads(int(torch_interop_threads))
        torch.use_deterministic_algorithms(True)
        self.torch = torch
        self.intergal_output = intergal_output
        self.device = torch.device(device)
        self.inference_chunk_size = int(inference_chunk_size)
        if self.inference_chunk_size <= 0:
            raise ValueError("inference_chunk_size must be positive")
        self.model = ModelConfig(config_path=str(config_path)).create()
        load_model_weights(self.model, str(weights_path), self.device)
        self.model.eval()

    def _predict_from_grids(
        self,
        point_grids: np.ndarray,
        areas: np.ndarray,
        aoa_deg: np.ndarray,
        mach: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        points = np.asarray(point_grids, dtype=np.float32)
        cells = np.asarray([point_grid_to_cell_grid(grid) for grid in point_grids], dtype=np.float32)
        areas = np.asarray(areas, dtype=np.float32)
        aoa_deg = np.asarray(aoa_deg, dtype=np.float32)
        mach = np.asarray(mach, dtype=np.float32)
        if not (len(points) == len(areas) == len(aoa_deg) == len(mach)):
            raise ValueError("grid, area, AoA, and Mach batches must have equal length")
        outputs: list[np.ndarray] = []
        with self.torch.inference_mode():
            for start in range(0, len(points), self.inference_chunk_size):
                stop = min(start + self.inference_chunk_size, len(points))
                point_tensor = self.torch.from_numpy(points[start:stop]).to(self.device)
                cell_tensor = self.torch.from_numpy(cells[start:stop]).to(self.device)
                condition = self.torch.from_numpy(
                    np.column_stack((aoa_deg[start:stop], mach[start:stop])).astype(np.float32)
                ).to(self.device)
                surface = self.model(cell_tensor, code=condition)[0]
                # The integrator accepts one scalar reference area, so integrate
                # members separately while retaining batched model inference.
                for local_index in range(stop - start):
                    force = self.intergal_output(
                        point_tensor[local_index : local_index + 1],
                        surface[local_index : local_index + 1],
                        condition[local_index : local_index + 1, 0],
                        s=float(areas[start + local_index]),
                        c=1.0,
                        xref=0.25,
                        yref=0.0,
                    )
                    outputs.append(force[0, :2].detach().cpu().numpy())
        coefficients = np.asarray(outputs, dtype=np.float64)
        return coefficients[:, 0], coefficients[:, 1]

    def fixed_cl(
        self,
        geometries_38d: np.ndarray,
        *,
        mach_grid: Sequence[float] = DEFAULT_MACH_GRID,
        cl_target: float = DEFAULT_CL_TARGET,
        aoa_seed_deg: float = DEFAULT_AOA_SEED_DEG,
        delta_deg: float = 0.5,
        tolerance: float = 1e-4,
        max_secant_updates: int = 4,
        aoa_bounds: tuple[float, float] = (-10.0, 25.0),
    ) -> FixedCLResult:
        geoms = np.asarray(geometries_38d, dtype=np.float64)
        if geoms.ndim == 1:
            geoms = geoms[None, :]
        if geoms.ndim != 2 or geoms.shape[1] != DESIGN_DIM or not np.all(np.isfinite(geoms)):
            raise ValueError("geometries must have shape (N, 38) and contain finite values")
        machs = np.asarray(mach_grid, dtype=np.float64)
        if machs.ndim != 1 or len(machs) == 0 or not np.all(np.isfinite(machs)):
            raise ValueError("mach_grid must be a non-empty finite sequence")
        point_grids = np.asarray([geometry_to_point_grid(geom) for geom in geoms])
        areas = np.asarray([reference_area(geom) for geom in geoms])
        n_wings, n_machs = len(geoms), len(machs)
        expanded_points = np.repeat(point_grids, n_machs, axis=0)
        expanded_areas = np.repeat(areas, n_machs)
        expanded_machs = np.tile(machs, n_wings)

        alpha0 = np.full(n_wings * n_machs, float(aoa_seed_deg), dtype=np.float64)
        alpha1 = np.full_like(alpha0, float(aoa_seed_deg + delta_deg))
        cl0, _ = self._predict_from_grids(expanded_points, expanded_areas, alpha0, expanded_machs)
        cl1, cd1 = self._predict_from_grids(expanded_points, expanded_areas, alpha1, expanded_machs)
        for _ in range(int(max_secant_updates)):
            denominator = cl1 - cl0
            valid_slope = np.abs(denominator) > 1e-8
            alpha2 = np.where(
                valid_slope,
                alpha1 + (float(cl_target) - cl1) * (alpha1 - alpha0) / denominator,
                alpha1,
            )
            alpha2 = np.clip(alpha2, float(aoa_bounds[0]), float(aoa_bounds[1]))
            cl2, cd2 = self._predict_from_grids(
                expanded_points, expanded_areas, alpha2, expanded_machs
            )
            alpha0, cl0 = alpha1, cl1
            alpha1, cl1, cd1 = alpha2, cl2, cd2

        converged = (
            np.isfinite(cl1)
            & np.isfinite(cd1)
            & (np.abs(cl1 - float(cl_target)) <= float(tolerance))
            & (cd1 > 0.0)
            & (alpha1 >= float(aoa_bounds[0]))
            & (alpha1 <= float(aoa_bounds[1]))
        )
        ld = np.where(converged, cl1 / cd1, np.nan)
        cl_matrix = cl1.reshape(n_wings, n_machs)
        cd_matrix = cd1.reshape(n_wings, n_machs)
        ld_matrix = ld.reshape(n_wings, n_machs)
        aoa_matrix = alpha1.reshape(n_wings, n_machs)
        converged_matrix = converged.reshape(n_wings, n_machs)
        all_valid = np.all(converged_matrix, axis=1) & np.all(ld_matrix > 0.0, axis=1)
        score = np.full(n_wings, -np.inf, dtype=np.float64)
        if np.any(all_valid):
            score[all_valid] = np.exp(np.mean(np.log(ld_matrix[all_valid]), axis=1))
        return FixedCLResult(
            cl=cl_matrix,
            cd=cd_matrix,
            ld=ld_matrix,
            aoa_deg=aoa_matrix,
            converged=converged_matrix,
            score=score,
        )
