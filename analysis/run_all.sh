#!/bin/bash
# Rebuild every table, figure and number file from the tables in data/.
# The baseline outputs are stamped with the data version set in common.py.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== 01_load: working tables from data/, with checks ==="
python3.11 01_load.py
echo
echo "=== 02_yield ==="
python3.11 02_yield.py
echo
echo "=== 03_recommendation_curves ==="
python3.11 03_recommendation_curves.py
echo
echo "=== 04_discovery_vs_recommendation ==="
python3.11 04_discovery_vs_recommendation.py
echo
echo "=== 05_paper_macros ==="
python3.11 05_paper_macros.py
echo
echo "=== 06_failure_predictability ==="
python3.11 06_failure_predictability.py
echo
echo "=== 07_solver_error ==="
python3.11 07_solver_error.py
echo
echo "=== 08_recommendation_curves_all: all five baselines ==="
python3.11 08_recommendation_curves_all.py
echo
echo "=== 09_failure_transfer ==="
python3.11 09_failure_transfer.py
echo
echo "=== 10_smac3_numbers: every SMAC3 number, and totals over all five baselines ==="
python3.11 10_smac3_numbers.py
echo
echo "=== 11_agent_endpoints: agent delivery and quality ==="
python3.11 11_agent_endpoints.py > /dev/null
echo "checked 90 episodes and 270 recommendation assessments"
echo
echo "=== 12_agent_cfd_outcomes: outcome types of every agent-study CFD request ==="
python3.11 12_agent_cfd_outcomes.py
echo
echo "=== 13_cfd_cost: processor-core hours per CFD evaluation, both studies ==="
python3.11 13_cfd_cost.py
echo
echo "=== 15_confirmatory_contrasts: the six planned baseline contrasts of Section 6.1 ==="
python3.11 15_confirmatory_contrasts.py
echo
echo "=== 16_marginal_intervals: unpaired intervals for every reported mean ==="
python3.11 16_marginal_intervals.py > /dev/null
echo "wrote analysis/out/tables/tab_marginal_intervals.csv"
echo
echo "=== 17_appendix_tables: every result table of the full-results appendix ==="
python3.11 17_appendix_tables.py > /dev/null
echo "wrote paper/app_results.tex"
echo
echo "=== 18_wing_gallery: the best delivered wing of every method and task, the gallery appendix ==="
python3.11 18_wing_gallery.py > /dev/null
echo "wrote paper/fig_wing_gallery.pdf"
echo
echo "=== 19_appendix_figures: the supplementary figures appendix, drawn in the paper style ==="
python3.11 19_appendix_figures.py > /dev/null
echo "wrote paper/app_fig_*.pdf"
echo
echo "=== done: tables in analysis/out/, figures in analysis/figures/, macros in analysis/out/macros/ and paper/ ==="
