# SuperWing 38-D design-vector schema

This is the layout of the 38-dimensional wing geometry vector used by the
SuperWing dataset and by every 3D-wing surrogate. It is **dataset
documentation** — the same parameterization spec the original researcher had at
v1, without which the dataset cannot be used. It describes what each dimension
*means*; it does not prescribe any defense.

A scalar 3D-wing surrogate's input is this 38-D vector with the two flight
conditions appended: `[geom(38), Mach, AoA]` → 40-D.

## Layout

| Index / slice | Meaning | Notes |
|---|---|---|
| `0`       | Leading-edge sweep angle (degrees) | used in transonic Mach corrections (`M_eff = M·cos Λ`) |
| `3`       | Aspect ratio (AR) | |
| `4`       | Taper ratio | tip chord / root chord |
| `7, 8, 9` | Section thickness ratios at 3 spanwise stations (root / mid / tip) | spanwise interpolation grid η = `[0.0, 0.5, 1.0]` |
| `13, 14, 15, 16` | Twist (degrees) at 4 spanwise stations | spanwise interpolation grid η = `[0.0, 1/3, 2/3, 1.0]` |
| `17 … 26` | Upper-surface CST (Kulfan) weights `Wu` (10 coefficients) | |
| `27 … 36` | Lower-surface CST (Kulfan) weights `Wl` (10 coefficients) | |
| `37`      | Root thickness factor (`tc_root`) | per-station thickness ≈ `tc_root · rtcs[station]` |

Indices `1, 2, 5, 6, 10, 11, 12` carry additional planform / shape parameters of
the SuperWing parameterization and are not individually broken out here.

## CST sections

The upper/lower surfaces use a standard class-shape (Kulfan/CST) representation
of order 9 (10 Bernstein coefficients each). At a spanwise station with thickness
ratio `r` (interpolated from indices 7/8/9 on the η = `[0, 0.5, 1]` grid), the
local section coefficients are `Wu·r` and `Wl·r`. Thickness at chordwise
fraction `x ∈ [0, 1]` follows the usual CST form
`y(x) = √x · (1−x) · Σ_i W_i · C(9,i) · x^i · (1−x)^(9−i)`.

## 2D-section reconstruction

To query a 2D-section surrogate from a 3D wing, sections are reconstructed at
chosen spanwise stations η: the local thickness-scaled CST coefficients (20
values) are concatenated with 8 context features
`[AoA_local, log10(Re), M_eff, η, sweep, AR, taper, M_global]`, where
`AoA_local = AoA + twist(η)` and `M_eff = M·cos(sweep)`. `Re` is taken at
`5×10⁶`. This 28-D vector is the 2D-section surrogate's input; the reconstruction
is performed for you by `ctx.query.sections(...)`.
