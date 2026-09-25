$campaign_message

PLANNING RESULT FOR THIS DECISION ($decision_id, planning cycle $cycle of at most $max_cycles)
Your planning stages selected $selected_label by the fixed comparison rule (records: $cycle_dir/selection.json).

SELECTED FIRST PROCEDURE, the one expensive evaluation to prepare now:
$procedure

FOLLOW-UP POLICY the plan intends after this evaluation returns. It is context only; do not carry it out now:
$followups

All records for this planning cycle are in $cycle_dir: the frozen root, the learner memo, every candidate, every evaluation and the selection. Scratch work from this cycle's stages is under $scratch_root. You may reuse any of it; say which files you reuse.

Carry out the selected procedure now. You may debug and refine within it. When it has produced its wing, hand over exactly one valid wing:
`python3 source/campaign.py hand-off --geom <file under work/> --reason "$plan_ref: <short reason>"`
Then write $report_path as JSON with status "handed_off", plan_ref, procedure_steps_done (a list) and deviations (refinements you made within the procedure, or "none"), and finish your turn.

$replan_instruction
