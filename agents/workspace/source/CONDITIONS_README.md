# Flight-condition records

Each wing in this dataset was tested at up to eight flight conditions. The
compact wing table has one row per wing and does **not** carry the operating
conditions. These files do.

## Files

| file | what it is |
|---|---|
| `running_info.jsonl` | the original run records, one JSON object per wing, exactly as received from the dataset authors. Nothing has been altered. |
| `conditions.jsonl` | one row per (wing, condition slot), every field copied from the original record. Includes the 38-D geometry and the raw `working_conditions` block. |
| `conditions.csv` | the same rows as a flat table, for the scalar fields only. Use the JSONL if you want the full `results` block or the geometry. |
| `superwing_public_dataset_v1.csv` | the compact one-row-per-wing table. |

`conditions.jsonl` and `conditions.csv` are derived from `running_info.jsonl` by
copying. No value was imputed, rounded, rescaled or filled in. If a field is
null here, the original record has no value there.

## Field mapping, from the original record

| this view | original path |
|---|---|
| `running_id` | `running_id` |
| `geometry_38d` | `shape` |
| `state` | `{k}_state` |
| `mach` | `{k}_running_info.working_conditions.M3` |
| `aoa_deg` | `{k}_running_info.working_conditions.AA` |
| `reynolds` | `{k}_running_info.working_conditions.re` |
| `temperature` | `{k}_running_info.working_conditions.temp` |
| `results` | `{k}_running_info.results` (includes `wing_cl`, `wing_cd`, `err_cl`, `n_step` and further diagnostics) |
| `raw_working_conditions` | the whole `working_conditions` block, unedited |
| `source_pointer` | where in the original record the row came from |

Units: `mach` dimensionless, `aoa_deg` degrees, `temperature` as recorded in the
source, `reynolds` as recorded in the source. The source does not state units for
the last two beyond the field names; they are passed through unchanged rather
than converted.

## Condition states, and what has values

| state | count | operating values present |
|---|---|---|
| `Success` | 28,856 | yes |
| `CL fluctuation too large` | 4,563 | yes |
| `Simulation Diverge` | 2,788 | yes |
| `Not Simulated` | 137 | **no** — nothing was run, so nothing was recorded |

A failed condition is a real record: it has Mach, angle of attack and a state,
and it tells you the solver was asked and did not give a usable answer. Failed
and not-run are different things and are not merged here.

## Two counts that are not the same number

* **36,352** possible slots: 4,544 wings times 8 condition indices.
* **36,344** recorded condition states.

The difference is eight slots on wing `1418` that are **absent from the record
entirely**. They are not eight failed solves. In `conditions.jsonl` and
`conditions.csv` those eight rows appear with `slot_recorded` false and every
other field null. Every other row has `slot_recorded` true.

## Provenance and publication

`running_info.jsonl` was supplied directly by the SuperWing authors and is **not
part of the public HuggingFace release**, which contains only converged results.
Internal use in this project is authorized. Publishing anything derived from this
file needs the authors' permission first. That question is separate from, and
unaffected by, any internal use here.

`load_conditions.py` in this directory prints one successful condition, one
failed condition and one absent slot, so you can see how each is represented.
