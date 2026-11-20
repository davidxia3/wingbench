#!/usr/bin/env python3
"""Resource accounting at recorded horizons. Never estimates matched rankings."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/out/resources"
METHODS = ["CMA-ES no feedback", "CMA-ES selection only", "CMA-ES feedback-guided",
           "DE feedback-guided", "SMAC3", "Open-ended research", "Planning", "Reflection"]
LABELS = dict(zip(METHODS, ["CMA-ES, no feedback", "CMA-ES, selection only", "Feedback-guided CMA-ES",
                          "Feedback-guided DE", "SMAC3", "Open-ended research", "Planning", "Reflection"]))
TOKEN_FIELDS = ["input_tokens", "output_tokens", "cache_read_input_tokens", "model_requests"]


def number(value: float, digits: int = 0) -> str:
    if pd.isna(value):
        return "---"
    return f"{value:,.{digits}f}".replace(",", "{,}")


def make_accounting() -> tuple[pd.DataFrame, pd.DataFrame]:
    ep = pd.read_csv(ROOT / "data/episodes.csv").set_index("episode")
    ev = pd.read_csv(ROOT / "data/evaluations.csv")
    turns = pd.read_csv(ROOT / "data/agent_turns.csv")
    turns["checkpoint"] = turns.decision.str.extract(r"q(\d+)$").astype(int)
    usage = pd.read_csv(ROOT / "data/surrogate_usage.csv")
    provenance = json.loads((ROOT / "data/surrogate_usage_provenance.json").read_text())
    for name, key in [("episodes.csv", "episodes_sha256"), ("surrogate_usage.csv", "extract_sha256")]:
        assert hashlib.sha256((ROOT / "data" / name).read_bytes()).hexdigest() == provenance[key]
    assert len(usage) == 420 and not usage.duplicated(["episode", "checkpoint"]).any()
    assert set(usage.episode) == set(ep.index)
    assert set(usage.source).issubset(provenance["source_sha256"])
    rows = []
    for u in usage.itertuples():
        e = ep.loc[u.episode]
        v = ev[(ev.episode == u.episode) & (((ev.kind == "online") & (ev["query"] <= u.checkpoint)) |
                                          ((ev.kind == "private") & (ev.checkpoint <= u.checkpoint)))]
        online = int((v.kind == "online").sum())
        assert online == u.checkpoint
        t = turns[(turns.episode == u.episode) & (turns.checkpoint <= u.checkpoint)]
        is_agent = e.study == "agent"
        if is_agent:
            assert len(t) > 0
        complete = u.checkpoint == e.online_queries
        row = dict(episode=u.episode, study=e.study, method=e.method, task=e.task,
                   checkpoint=u.checkpoint, online_cfd=online,
                   private_cfd=int((v.kind == "private").sum()),
                   prior_development_records=62 if e.study == "baseline" and e.method != "SMAC3" else 0,
                   surrogate_cap=0 if e.method == "SMAC3" else 4096,
                   surrogate_evaluations=int(u.surrogate_evaluations),
                   agent_sessions=len(t), sessions_missing_tokens=int(t[TOKEN_FIELDS].isna().any(axis=1).sum()),
                   agent_session_seconds=float(t.wall_seconds.sum()) if is_agent else None,
                   reference_model_cost_usd=float(t.cost_usd.sum()) if is_agent else 0.0,
                   cfd_core_seconds=float(e.cfd_core_seconds) if complete else None,
                   timed_cfd_records=int(e.timed_evaluations) if complete else None)
        row.update({key: float(t[key].sum(min_count=1)) if is_agent else 0 for key in TOKEN_FIELDS})
        rows.append(row)
    details = pd.DataFrame(rows)
    summaries = []
    for method in METHODS:
        for q in sorted(details.loc[details.method == method, "checkpoint"].unique()):
            d = details[(details.method == method) & (details.checkpoint == q)]
            assert len(d) == 30 and d.groupby("task").size().to_dict() == dict(high=10, low=10, nominal=10)
            sums = {key: d[key].sum(min_count=1) for key in
                    ["online_cfd", "private_cfd", "agent_sessions", "sessions_missing_tokens",
                     "reference_model_cost_usd", "agent_session_seconds", "cfd_core_seconds", "timed_cfd_records"] + TOKEN_FIELDS}
            summaries.append(dict(method=method, checkpoint=int(q), episodes=len(d),
                                  surrogate_mean=d.surrogate_evaluations.mean(),
                                  surrogate_min=d.surrogate_evaluations.min(),
                                  surrogate_max=d.surrogate_evaluations.max(),
                                  session_minutes_per_online=sums["agent_session_seconds"] / 60 / sums["online_cfd"],
                                  **sums))
    summary = pd.DataFrame(summaries)
    final = details.merge(ep[["online_queries"]], left_on="episode", right_index=True)
    final = final[final.checkpoint == final.online_queries]
    assert len(final) == 240
    assert final.online_cfd.sum() == 6000 and final.private_cfd.sum() == 629
    assert abs(final.reference_model_cost_usd.sum() - turns.cost_usd.sum()) < 1e-8
    assert abs(final.cfd_core_seconds.sum() - ep.cfd_core_seconds.sum()) < 1e-6
    for key in TOKEN_FIELDS:
        assert final[key].sum() == turns[key].sum()
    assert final.sessions_missing_tokens.sum() == 11
    return details, summary


def appendix(summary: pd.DataFrame) -> str:
    lines = [r"% Generated by analysis/20_resource_accounting.py from released tables.",
             r"\section{Resource conditions and recorded usage}", r"\label{app:resources}",
             r"The following tables account for recorded usage; they do not estimate rankings under equal resources. Each method has 30 episodes (ten per task). The query-4 rows describe prefixes of a 32-query task, not runs instructed to stop at four. The open-ended query-32 row continues its query-4 episodes and must not be added to that row.",
             r"\subsection{Surrogate and CFD requests}",
             r"Table~\ref{tab:resource-queries} separates prior information, online requests, private recommendation grades, and actual surrogate usage. The four surrogate-assisted baselines receive the same 62 development records; agents receive SuperWing source records but not this 62-record context. SMAC3 has neither the warm start nor surrogate access. Private grades are not returned to the method. They are additional recorded requests, not necessarily new solver executions.",
             r"\begin{table}[htbp]\centering\scriptsize",
             r"\caption{Recorded requests through each checkpoint. Surrogate entries are mean [minimum, maximum] charged candidate evaluations per episode, with a 4,096 episode cap except for SMAC3. CFD columns are totals over 30 episodes.}",
             r"\label{tab:resource-queries}", r"\setlength{\tabcolsep}{3pt}",
             r"\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lrrrr}\toprule",
             r"Method & Checkpoint & Surrogate / episode & Online CFD & Private CFD \\ \midrule"]
    for q in (4, 32):
        for method in METHODS:
            d = summary[(summary.method == method) & (summary.checkpoint == q)]
            if d.empty:
                continue
            r = d.iloc[0]
            usage = f"{number(r.surrogate_mean, 1)} [{number(r.surrogate_min)}, {number(r.surrogate_max)}]"
            lines.append(f"{LABELS[method]} & {q} & {usage} & {number(r.online_cfd)} & {number(r.private_cfd)} " + r"\\")
        if q == 4:
            lines.append(r"\midrule")
    lines.extend([r"\bottomrule\end{tabular*}\end{table}",
                  r"The archive methods spend all 4,096 surrogate evaluations before online CFD; the adaptive baseline methods have spent 512 at query 4 and 4,096 at query 32. Agents choose their own usage. Agent counts sum committed ledger charges; exact repeats served from the episode cache are free and are excluded. Thus charged evaluations are not the number of tool invocations. Baseline counts come from checkpoint budget counters; SMAC3's zero follows from its CFD-only implementation. These accounting rules and the different information sets remain limitations of cross-family comparisons.",
                  r"\subsection{Agent sessions and model usage}",
                  r"Table~\ref{tab:resource-model} counts all launched sessions assigned to decisions through the checkpoint, including checkpoint-0 reviews, later reviews, reflection, errors and retries. Planning's nested model calls are included in its research sessions. Summed session elapsed time includes tool execution, local code, and waiting within sessions; it is neither pure model inference time nor end-to-end campaign wall time, and overlapping sessions need not be additive elapsed time. Token counts are provider-reported and incomplete for eleven sessions across the whole study. Cache-read tokens are reported separately. Dollar values are reference model charges, excluding CFD and hosting.",
                  r"\begin{table}[htbp]\centering\scriptsize",
                  r"\caption{Recorded agent usage at a common four-query checkpoint and for the open-ended extension. Tokens are totals in millions across 30 episodes. Time is summed session minutes divided by online CFD requests. Missing-token sessions still contribute their recorded time and cost.}",
                  r"\label{tab:resource-model}", r"\setlength{\tabcolsep}{2pt}",
                  r"\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lrrrrrrrr}\toprule",
                  r"Method & $q$ & Input & Output & Cache & \shortstack{Model\\calls} & \shortstack{Minutes\\/ CFD} & \shortstack{Cost\\(\$)} & \shortstack{Missing\\sessions} \\ \midrule"])
    for method, q in [("Open-ended research", 4), ("Planning", 4), ("Reflection", 4), ("Open-ended research", 32)]:
        r = summary[(summary.method == method) & (summary.checkpoint == q)].iloc[0]
        vals = [LABELS[method], str(q)] + [number(r[key] / 1e6, 2) for key in TOKEN_FIELDS[:3]]
        vals += [number(r.model_requests), number(r.session_minutes_per_online, 2),
                 number(r.reference_model_cost_usd, 2), number(r.sessions_missing_tokens)]
        lines.append(" & ".join(vals) + r" \\")
    lines.extend([r"\bottomrule\end{tabular*}\end{table}",
                  r"\subsection{CFD timing and unmeasured resources}",
                  r"Table~\ref{tab:resource-cfd-time} uses the existing episode timing aggregates: summed mesh and flow-attempt wall seconds multiplied by 16 cores. These include online and private grading. They describe timing attached to evaluation records, not unique physical compute or a cloud bill: outcomes can be reused, some records have no timing, and the SMAC3 timing aggregate retains two superseded grades. The aggregates do not provide a separate query-4 timing total for the open-ended extension or the baselines. They also do not establish identical hardware across campaigns.",
                  r"\begin{table}[htbp]\centering\scriptsize",
                  r"\caption{CFD timing at each method's recorded final horizon. These rows cover different query horizons. No matched-runtime comparison is inferred.}",
                  r"\label{tab:resource-cfd-time}", r"\setlength{\tabcolsep}{4pt}",
                  r"\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lrrr}\toprule",
                  r"Method & Final query & Timed records & Recorded core-hours \\ \midrule"])
    for method in METHODS:
        r = summary[summary.method == method].sort_values("checkpoint").iloc[-1]
        lines.append(" & ".join([LABELS[method], str(int(r.checkpoint)), number(r.timed_cfd_records),
                                  number(r.cfd_core_seconds / 3600, 1)]) + r" \\")
    lines.extend([r"\bottomrule\end{tabular*}\end{table}",
                  r"Optimizer CPU/GPU time, model-server compute, and consistent end-to-end episode wall time were not recorded across all methods. Missing values are not treated as zero. Consequently, neither equal CFD caps nor this accounting supplies a ranking under equal total computation or wall-clock limits. Such a ranking would require a new experiment with a common initial information set, resource rules and recommendation endpoint; it cannot be reconstructed by subtracting prior records or truncating costs after the fact.",
                  r"\paragraph{Reproduction.} \texttt{analysis/20\_resource\_accounting.py} reads the released episode, evaluation and agent-session tables plus \texttt{data/surrogate\_usage.csv}, and writes per-episode and method-level tables and this appendix. The surrogate extract includes checkpoint counts for all 240 episodes and source fingerprints in \texttt{data/surrogate\_usage\_provenance.json}. The optional archive extractor is supplied separately; the original campaign archives are not required to reproduce the accounting tables.", ""])
    # Keep floats outside paragraphs so LaTeX cannot insert a table mid-sentence.
    return "\n".join(lines).replace("\n\\begin{table}", "\n\n\\begin{table}").replace(
        "\\end{table}\n", "\\end{table}\n\n")


def main() -> None:
    details, summary = make_accounting()
    OUT.mkdir(parents=True, exist_ok=True)
    details.to_csv(OUT / "per_episode.csv", index=False, float_format="%.6f")
    summary.to_csv(OUT / "by_method.csv", index=False, float_format="%.6f")
    (ROOT / "paper/app_resources.tex").write_text(appendix(summary))
    print(summary[["method", "checkpoint", "surrogate_mean", "session_minutes_per_online", "reference_model_cost_usd"]].to_string(index=False))
    print("Verified 240 episodes, 6,000 online and 629 private records; all recorded model usage and CFD timing reconcile.")


if __name__ == "__main__":
    main()
