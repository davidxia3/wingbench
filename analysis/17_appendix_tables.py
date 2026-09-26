#!/usr/bin/env python3
"""17_appendix_tables.py: the full results tables of the appendix.

Every number comes from three tables in data/: episodes.csv, evaluations.csv
and recommendations.csv. The script writes one LaTeX fragment,
paper/app_results.tex, which is a self-contained appendix section, and a CSV
twin of each table body under analysis/out/tables/tab_appendix_<name>.csv.

The tables, in the order they appear in the fragment:

  baseline_recommendations  how many of the ten recommended wings delivered
                            and the mean L/D of those that did, for every
                            baseline method, task and checkpoint 0 to 32
  agent_recommendations     the same for the agents at checkpoints 0 to 4
  online_delivery           delivered online queries, total and rate per
                            method and task. The agents' four-query comparison
                            (queries 1 to 4 of all 90 episodes) and the
                            32-query extension (all 32 queries of the 30
                            continued episodes) are listed separately
  online_outcomes           the same online queries by outcome type
  compute                   CFD core-hours per method, from episodes.csv
  episodes                  one row per episode as a longtable. It comes last
                            because it runs over several pages

Rounding: L/D and rates to two decimals, core-hours per evaluation to one,
total core-hours to whole hours. A missing value is shown as --. The CSV twins
hold the displayed, rounded values and use the method and task names of data/.
Rows follow the paper's order of studies, methods and tasks, then the seed.
The fragment needs booktabs, longtable and amsmath, so the main document must
load longtable.

Run from the repository root:  python3 analysis/17_appendix_tables.py

Input:  data/episodes.csv, data/evaluations.csv, data/recommendations.csv
Output: paper/app_results.tex
        analysis/out/tables/tab_appendix_<name>.csv
"""
from __future__ import annotations

import pandas as pd

from common import TABLES, WORKSPACE, ensure_dirs, read_table

FRAGMENT = WORKSPACE / "paper" / "app_results.tex"

BASELINES = ["CMA-ES no feedback", "CMA-ES selection only", "CMA-ES feedback-guided",
             "DE feedback-guided", "SMAC3"]
AGENTS = ["Open-ended research", "Planning", "Reflection"]
METHODS = {"baseline": BASELINES, "agent": AGENTS}
DISPLAY = {"CMA-ES no feedback": "CMA-ES, no feedback",
           "CMA-ES selection only": "CMA-ES, selection only",
           "CMA-ES feedback-guided": "Feedback-guided CMA-ES",
           "DE feedback-guided": "Feedback-guided DE"}
TASKS = ["low", "nominal", "high"]
TASK_CL = {"low": "0.15", "nominal": "0.41", "high": "0.66"}
CHECKPOINTS = {"baseline": [0, 1, 2, 4, 8, 16, 32], "agent": [0, 1, 2, 4]}
LAST_CHECKPOINT = {"baseline": 32, "agent": 4}
COMPARISON_QUERIES = 4   # the agent comparison used queries 1 to 4 of every episode
EXTENSION_QUERIES = 32   # the continued agent episodes ran to 32 queries
EPISODES_PER_CELL = 10
OUTCOMES = ["pass", "partial", "undetermined", "conclusive_failure"]
OUTCOME_LABEL = {"pass": "pass", "partial": "partial", "undetermined": "undetermined",
                 "conclusive_failure": "conclusive failure"}
MISSING = "--"

TEXT = {
    "opener": (
        "The tables in this appendix give the full results per method, task and episode. "
        r"They are computed from the released data tables by \texttt{analysis/17\_appendix\_tables.py}."),
    "baseline_recommendations": (
        r"Table~\ref{tab:app-baseline-recommendations} gives, for every baseline method, task and "
        r"checkpoint, how many of the ten recommended wings delivered a CFD score and the mean $L/D$ "
        "of those that did.",
        "Baseline recommendations at every checkpoint. A checkpoint is the number of online CFD "
        "queries the method had made when it recommended the wing, so checkpoint 0 is the "
        "recommendation made before any CFD feedback. For each method and task, $n$ is the number of "
        "the ten episodes whose recommended wing delivered a CFD score, and $L/D$ is the mean score of "
        "those delivered recommendations."),
    "agent_recommendations": (
        r"Table~\ref{tab:app-agent-recommendations} gives the same for the three agent methods at the "
        "checkpoints of the four-query comparison.",
        "Agent recommendations at checkpoints 0, 1, 2 and 4, in the format of "
        r"Table~\ref{tab:app-baseline-recommendations}."),
    "online_delivery": (
        r"Table~\ref{tab:app-online-delivery} counts the online CFD queries of each method and task "
        "that delivered a score.",
        "Online CFD delivery per method and task. Delivered is the number of online queries that "
        "returned a valid score, total is the number of online queries, and rate is their ratio. The "
        "agent rows cover queries 1 to 4 of all 90 agent episodes. The extension row covers all 32 "
        "queries of the 30 open-ended research episodes that continued, so it counts the first four "
        "queries of those episodes a second time."),
    "online_outcomes": (
        r"Table~\ref{tab:app-online-outcomes} splits the same online queries by the grader's outcome type.",
        r"Outcome types of the online CFD queries, in the groups of Table~\ref{tab:app-online-delivery}. "
        "A pass delivered a score. A partial outcome has a result at some but not all flight "
        "conditions. An undetermined outcome has no result at any condition. A conclusive failure is "
        "a rejected geometry. The SMAC3 records name the reason for each nondelivery but not the "
        "outcome type, so its type counts are empty."),
    "compute": (
        r"Table~\ref{tab:app-compute} sums the CFD processor time of each method over the evaluations "
        "that have a recorded run time.",
        "CFD processor time per method. A core-hour is one hour of one processor core, and each CFD "
        "job used 16 cores. The totals cover the evaluations with a recorded run time. The SMAC3 total "
        "includes two checkpoint-32 grades that were replaced when two SMAC3 episodes were run again."),
    "episodes": (
        r"Table~\ref{tab:app-episodes} lists all 240 episodes.",
        "All 240 episodes. The online columns count the episode's online CFD queries, how many "
        r"delivered a score, and the best delivered $L/D$. The final recommendation is the one made at "
        "the last checkpoint, after 32 queries for the baselines and after 4 for the agents. The 30 "
        "open-ended research episodes continued to 32 online queries, and their online columns cover "
        "all 32."),
}


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def name(method: str) -> str:
    return DISPLAY.get(method, method)


def cl(task: str) -> str:
    return f"${TASK_CL[task]}$"


def ld(value) -> str:
    return MISSING if value is None or pd.isna(value) else f"{float(value):.2f}"


def comma(n: int) -> str:
    return f"{n:,}".replace(",", "{,}")


def round2(value):
    return None if value is None or pd.isna(value) else round(float(value), 2)


def table_env(label: str, caption: str, colspec: str, body: list[str], tabcolsep: str = "4pt") -> str:
    return "\n".join([r"\begin{table}[htbp]", r"\caption{" + caption + "}", r"\label{" + label + "}",
                      r"\centering", r"\scriptsize", r"\setlength{\tabcolsep}{" + tabcolsep + "}",
                      r"\begin{tabular}{" + colspec + "}", r"\toprule", *body, r"\bottomrule",
                      r"\end{tabular}", r"\end{table}"])


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------
def load():
    episodes = read_table("episodes")
    evaluations = read_table("evaluations")
    recommendations = read_table("recommendations")
    for df in (evaluations, recommendations):
        df["delivered"] = df.delivered.astype(str).str.lower().eq("true")
    check(episodes, evaluations, recommendations)
    return episodes, evaluations, recommendations


def check(episodes, evaluations, recommendations) -> None:
    """The study structure the tables assume."""
    assert len(episodes) == 240, len(episodes)
    for study, methods in METHODS.items():
        for method in methods:
            for task in TASKS:
                cell = episodes[(episodes.study == study) & (episodes.method == method) & (episodes.task == task)]
                assert len(cell) == EPISODES_PER_CELL and cell.seed.is_unique, (study, method, task, len(cell))
    online = evaluations[evaluations.kind == "online"]
    counts = online.groupby("episode").size().reindex(episodes.episode)
    assert counts.eq(episodes.online_queries.to_numpy()).all(), "online query counts differ from episodes.csv"
    assert not online.duplicated(["episode", "query"]).any()
    assert evaluations.ld.notna().eq(evaluations.delivered).all(), "a score without delivery or the reverse"
    assert recommendations.ld.notna().eq(recommendations.delivered).all()
    per_cell = recommendations.groupby(["study", "method", "task", "checkpoint"]).size()
    assert per_cell.eq(EPISODES_PER_CELL).all(), per_cell[per_cell != EPISODES_PER_CELL]
    for study, checkpoints in CHECKPOINTS.items():
        assert sorted(recommendations[recommendations.study == study].checkpoint.unique()) == checkpoints


# ---------------------------------------------------------------------------
# recommendations at each checkpoint (tables a and b)
# ---------------------------------------------------------------------------
def recommendation_rows(recommendations, study: str):
    rows = []
    for task in TASKS:
        for method in METHODS[study]:
            for checkpoint in CHECKPOINTS[study]:
                cell = recommendations[(recommendations.study == study) & (recommendations.method == method)
                                       & (recommendations.task == task) & (recommendations.checkpoint == checkpoint)]
                delivered = int(cell.delivered.sum())
                rows.append(dict(study=study, method=method, task=task, checkpoint=checkpoint,
                                 episodes=len(cell), delivered=delivered,
                                 mean_ld=round2(cell.ld[cell.delivered].mean()) if delivered else None))
    return pd.DataFrame(rows)


def recommendation_table(rows, study: str, label: str, caption: str) -> str:
    checkpoints = CHECKPOINTS[study]
    k = len(checkpoints)
    width = 1 + 2 * k
    body = [r"method & \multicolumn{%d}{c}{online CFD queries before the recommendation} \\" % (2 * k),
            r"\cmidrule(lr){2-%d}" % width,
            " & " + " & ".join(r"\multicolumn{2}{c}{%d}" % c for c in checkpoints) + r" \\",
            "".join(r"\cmidrule(lr){%d-%d}" % (2 + 2 * i, 3 + 2 * i) for i in range(k)),
            " & " + " & ".join([r"$n$ & $L/D$"] * k) + r" \\",
            r"\midrule"]
    for i, task in enumerate(TASKS):
        if i:
            body.append(r"\addlinespace")
        body.append(r"\multicolumn{%d}{l}{$C_L=%s$} \\" % (width, TASK_CL[task]))
        for method in METHODS[study]:
            cells = []
            for checkpoint in checkpoints:
                r = rows[(rows.method == method) & (rows.task == task) & (rows.checkpoint == checkpoint)].iloc[0]
                cells.append(f"{int(r.delivered)} & {ld(r.mean_ld)}")
            body.append(name(method) + " & " + " & ".join(cells) + r" \\")
    return table_env(label, caption, "l" + "rr" * k, body, tabcolsep="3pt")


# ---------------------------------------------------------------------------
# online queries (tables c and d)
# ---------------------------------------------------------------------------
def online_blocks(episodes, evaluations):
    """The three groups of online queries: the baselines, the agents' four-query
    comparison, and the 32-query extension of the continued agent episodes."""
    online = evaluations[evaluations.kind == "online"]
    continued = episodes[(episodes.study == "agent") & (episodes.online_queries == EXTENSION_QUERIES)]
    assert len(continued) == 30, len(continued)
    return [
        ("baseline", "Baselines, 32 queries in each of 150 episodes", BASELINES,
         online[online.study == "baseline"]),
        ("agent_comparison", "Agents, queries 1 to 4 of all 90 episodes", AGENTS,
         online[(online.study == "agent") & (online["query"] <= COMPARISON_QUERIES)]),
        ("agent_extension", "Extension, all 32 queries of the 30 continued episodes",
         [m for m in AGENTS if m in set(continued.method)],
         online[online.episode.isin(continued.episode)]),
    ]


def delivery_rows(blocks):
    rows = []
    for block, _, methods, frame in blocks:
        for method in methods:
            for task in TASKS:
                cell = frame[(frame.method == method) & (frame.task == task)]
                n, d = len(cell), int(cell.delivered.sum())
                assert n > 0, (block, method, task)
                rows.append(dict(block=block, method=method, task=task, delivered=d, evaluations=n,
                                 rate=round(d / n, 2)))
    return pd.DataFrame(rows)


def delivery_table(rows, blocks, label: str, caption: str) -> str:
    body = ["method & " + " & ".join(r"\multicolumn{3}{c}{$C_L=%s$}" % TASK_CL[t] for t in TASKS) + r" \\",
            "".join(r"\cmidrule(lr){%d-%d}" % (2 + 3 * i, 4 + 3 * i) for i in range(len(TASKS))),
            " & " + " & ".join(["delivered & total & rate"] * len(TASKS)) + r" \\",
            r"\midrule"]
    for i, (block, title, methods, _) in enumerate(blocks):
        if i:
            body.append(r"\addlinespace")
        body.append(r"\multicolumn{10}{l}{\emph{%s}} \\" % title)
        for method in methods:
            cells = []
            for task in TASKS:
                r = rows[(rows.block == block) & (rows.method == method) & (rows.task == task)].iloc[0]
                cells.append(f"{int(r.delivered)} & {int(r.evaluations)} & {r.rate:.2f}")
            body.append(name(method) + " & " + " & ".join(cells) + r" \\")
    return table_env(label, caption, "l" + "rrr" * len(TASKS), body)


def outcome_rows(blocks):
    rows = []
    for block, _, methods, frame in blocks:
        for method in methods:
            for task in TASKS:
                cell = frame[(frame.method == method) & (frame.task == task)]
                typed = cell.outcome.notna()
                row = dict(block=block, method=method, task=task)
                if typed.all():
                    assert cell.outcome.isin(OUTCOMES).all(), cell.outcome.unique()
                    for outcome in OUTCOMES:
                        row[outcome] = int((cell.outcome == outcome).sum())
                else:  # a method's records either name the outcome type or do not
                    assert not typed.any(), (block, method, task)
                    for outcome in OUTCOMES:
                        row[outcome] = None
                row["total"] = len(cell)
                rows.append(row)
    table = pd.DataFrame(rows)
    table[OUTCOMES] = table[OUTCOMES].astype("Int64")
    return table


def outcome_table(rows, blocks, label: str, caption: str) -> str:
    body = ["method & $C_L$ & " + " & ".join(OUTCOME_LABEL[o] for o in OUTCOMES) + r" & total \\", r"\midrule"]
    for i, (block, title, methods, _) in enumerate(blocks):
        if i:
            body.append(r"\addlinespace")
        body.append(r"\multicolumn{7}{l}{\emph{%s}} \\" % title)
        for method in methods:
            for j, task in enumerate(TASKS):
                r = rows[(rows.block == block) & (rows.method == method) & (rows.task == task)].iloc[0]
                counts = [MISSING if pd.isna(r[o]) else str(int(r[o])) for o in OUTCOMES]
                body.append((name(method) if j == 0 else "") + f" & {cl(task)} & " + " & ".join(counts)
                            + f" & {int(r.total)} \\\\")
    return table_env(label, caption, "llrrrrr", body)


# ---------------------------------------------------------------------------
# compute time (table f)
# ---------------------------------------------------------------------------
def compute_rows(episodes):
    rows = []

    def add(study, method, frame):
        seconds = float(frame.cfd_core_seconds.sum())
        timed = int(frame.timed_evaluations.sum())
        rows.append(dict(study=study, method=method, episodes=len(frame), timed_evaluations=timed,
                         core_hours=round(seconds / 3600),
                         core_hours_per_evaluation=round(seconds / 3600 / timed, 1)))

    for study, methods in METHODS.items():
        for method in methods:
            add(study, method, episodes[(episodes.study == study) & (episodes.method == method)])
        add(study, "all", episodes[episodes.study == study])
    add("both", "all", episodes)
    return pd.DataFrame(rows)


def compute_table(rows, label: str, caption: str) -> str:
    def line(text, r):
        return (f"{text} & {int(r.episodes)} & {comma(int(r.timed_evaluations))} & "
                f"{comma(int(r.core_hours))} & {r.core_hours_per_evaluation:.1f} \\\\")

    body = [r"method & episodes & timed evaluations & core-hours & core-hours per evaluation \\", r"\midrule"]
    for i, (study, title) in enumerate((("baseline", "Baselines"), ("agent", "Agents"))):
        if i:
            body.append(r"\addlinespace")
        body.append(r"\multicolumn{5}{l}{\emph{%s}} \\" % title)
        for method in METHODS[study]:
            body.append(line(name(method), rows[(rows.study == study) & (rows.method == method)].iloc[0]))
        body.append(line("All " + title.lower(), rows[(rows.study == study) & (rows.method == "all")].iloc[0]))
    body.append(r"\midrule")
    body.append(line("Both studies", rows[rows.study == "both"].iloc[0]))
    return table_env(label, caption, "lrrrr", body)


# ---------------------------------------------------------------------------
# one row per episode (table e)
# ---------------------------------------------------------------------------
def episode_rows(episodes, evaluations, recommendations):
    online = evaluations[evaluations.kind == "online"]
    summary = online.groupby("episode").agg(online_evaluations=("evaluation", "size"),
                                            online_delivered=("delivered", "sum"),
                                            best_online_ld=("ld", "max"))
    rows = []
    for study, methods in METHODS.items():
        last = LAST_CHECKPOINT[study]
        for method in methods:
            for task in TASKS:
                cell = episodes[(episodes.study == study) & (episodes.method == method) & (episodes.task == task)]
                for e in cell.sort_values("seed").itertuples():
                    s = summary.loc[e.episode]
                    final = recommendations[(recommendations.episode == e.episode)
                                            & (recommendations.checkpoint == last)]
                    assert len(final) == 1, (e.episode, last, len(final))
                    final = final.iloc[0]
                    rows.append(dict(episode=e.episode, study=study, method=method, task=task, seed=int(e.seed),
                                     online_evaluations=int(s.online_evaluations),
                                     online_delivered=int(s.online_delivered),
                                     best_online_ld=round2(s.best_online_ld), final_checkpoint=last,
                                     final_delivered=bool(final.delivered), final_ld=round2(final.ld)))
    return pd.DataFrame(rows)


def episode_longtable(rows, label: str, caption: str) -> str:
    head = [r"\toprule",
            r" & & & & \multicolumn{3}{c}{online CFD queries} & \multicolumn{2}{c}{final recommendation} \\",
            r"\cmidrule(lr){5-7}\cmidrule(lr){8-9}",
            r"episode & method & $C_L$ & seed & total & delivered & best $L/D$ & delivered & $L/D$ \\",
            r"\midrule"]
    lines = [r"\begingroup", r"\scriptsize", r"\setlength{\tabcolsep}{4pt}",
             r"\begin{longtable}{lllrrrrlr}",
             r"\caption{" + caption + r"}\label{" + label + r"} \\",
             *head, r"\endfirsthead",
             *head, r"\endhead",
             r"\midrule", r"\multicolumn{9}{r}{continued on the next page} \\", r"\endfoot",
             r"\bottomrule", r"\endlastfoot"]
    previous = None
    for r in rows.itertuples():
        group = (r.study, r.method, r.task)
        if previous is not None and group != previous:
            lines.append(r"\addlinespace")
        previous = group
        lines.append(f"{r.episode} & {name(r.method)} & {cl(r.task)} & {r.seed} & {r.online_evaluations} & "
                     f"{r.online_delivered} & {ld(r.best_online_ld)} & {'yes' if r.final_delivered else 'no'} & "
                     f"{ld(r.final_ld)} \\\\")
    lines += [r"\end{longtable}", r"\endgroup"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def main() -> None:
    ensure_dirs()
    episodes, evaluations, recommendations = load()
    blocks = online_blocks(episodes, evaluations)

    tables = {
        "baseline_recommendations": recommendation_rows(recommendations, "baseline"),
        "agent_recommendations": recommendation_rows(recommendations, "agent"),
        "online_delivery": delivery_rows(blocks),
        "online_outcomes": outcome_rows(blocks),
        "compute": compute_rows(episodes),
        "episodes": episode_rows(episodes, evaluations, recommendations),
    }
    for stem, df in tables.items():
        df.to_csv(TABLES / f"tab_appendix_{stem}.csv", index=False)

    def entry(stem: str, tex: str) -> list[str]:
        return [TEXT[stem][0], "", tex, ""]

    def cap(stem: str) -> str:
        return TEXT[stem][1]

    parts = ["% Generated by analysis/17_appendix_tables.py from data/episodes.csv,",
             "% data/evaluations.csv and data/recommendations.csv. Do not edit by hand.",
             "% Needs booktabs, longtable and amsmath.",
             r"\section{Full results tables}", r"\label{app:fulltables}", "", TEXT["opener"], ""]
    parts += entry("baseline_recommendations", recommendation_table(
        tables["baseline_recommendations"], "baseline", "tab:app-baseline-recommendations",
        cap("baseline_recommendations")))
    parts += entry("agent_recommendations", recommendation_table(
        tables["agent_recommendations"], "agent", "tab:app-agent-recommendations", cap("agent_recommendations")))
    parts += entry("online_delivery", delivery_table(
        tables["online_delivery"], blocks, "tab:app-online-delivery", cap("online_delivery")))
    parts += entry("online_outcomes", outcome_table(
        tables["online_outcomes"], blocks, "tab:app-online-outcomes", cap("online_outcomes")))
    parts += entry("compute", compute_table(tables["compute"], "tab:app-compute", cap("compute")))
    parts += [r"\clearpage", ""]
    parts += entry("episodes", episode_longtable(tables["episodes"], "tab:app-episodes", cap("episodes")))
    FRAGMENT.write_text("\n".join(parts).rstrip() + "\n")

    print(f"wrote {FRAGMENT.relative_to(WORKSPACE)}")
    for stem, df in tables.items():
        print(f"wrote {(TABLES / f'tab_appendix_{stem}.csv').relative_to(WORKSPACE)} ({len(df)} rows)")
    c = tables["compute"]
    both = c[c.study == "both"].iloc[0]
    print(f"episodes {len(tables['episodes'])}, timed evaluations {int(both.timed_evaluations)}, "
          f"core-hours {int(both.core_hours)}, per evaluation {both.core_hours_per_evaluation:.1f}")


if __name__ == "__main__":
    main()
