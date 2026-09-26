# Agent prompts and task files

This folder holds what the three agent methods were told: the task every agent
received, the tool it used to query the surrogate and hand off a wing, and the
extra prompts used by the reflection and planning methods.

| Path | What it is |
| --- | --- |
| `workspace/TASK.md` | The task description at the top of every agent workspace |
| `workspace/campaign.py` | The tool every agent used to read the task, query the surrogate, check its budget, and hand off one wing for CFD |
| `workspace/source/` | The rest of the agent's `source/` folder: the guide to the 38 design variables, the guide to the flight conditions, the result schema, the normalization constants, and the helper scripts to load conditions, convert geometry, and draw a wing |
| `prompts/manager_messages.md` | The fixed system prompt and every message the campaign manager sent at the start of a turn, quoted from the code that ran |
| `tasks/task_documents.json` | The settings of each task: target lift coefficient, angle-of-attack seed, Mach numbers, objective, score rule, and budgets |
| `prompts/reflection/reflect.md` | Reflection. A fresh session reads the result that just returned and writes lessons for the next turn |
| `prompts/planning/learner.md` | Planning step 1. Writes a memo of what the results so far show |
| `prompts/planning/planner.md` | Planning step 2. Each of four planners writes one two-step plan |
| `prompts/planning/evaluator.md` | Planning step 3. Predicts the delivery and L/D of each plan's steps |
| `prompts/planning/executor.md` | Planning step 5. The main session carries out step 1 of the selected plan |
| `prompts/planning/correction.md` | Sent when a planning stage's reply did not match the required format |

Step 4 of planning has no prompt. A fixed formula keeps the plan with the best
expected L/D. Open-ended research uses no extra prompt. It works from the task
files alone.

The planning stages had to answer in a fixed JSON form, which the loop checked
before accepting a reply. A plan has a `summary`, a first action `epoch1`, one
follow-up action per possible outcome of the first action, a `rationale`, and a
list of `branch_artifacts`. An action has three fields: `procedure`, the text
of what to do, `surrogate_evaluations_planned`, a whole number, and
`wing_selection`, the rule for choosing the wing to hand over. The evaluator
gives each action a prediction with `p_complete`, the chance of a complete
evaluation, `ld_q10`, `ld_q50` and `ld_q90`, three quantiles of the expected
L/D, and a `basis`. Step 4 scores each plan by its expected best delivered L/D over a small
tree of outcomes. With probability `p_complete` the first action delivers a
score, taken as one of the three predicted quantiles, and otherwise it delivers
nothing, which counts as zero unless a better wing is already in hand. A
two-step plan treats its follow-up action the same way and averages the best
score after each step. The plan with the highest expected value is kept, and
ties go to the plan whose first action is more likely to complete. The learner
memo lists measured facts from online and surrogate results, beliefs with
evidence and a confidence of low, medium or high, prediction calibration
records, reusable artifacts, and a budget note. The 104 placeholder plans the
paper reports are plans whose `procedure` or `wing_selection` text was a
placeholder rather than an instruction.

## How the agents ran

All three methods used DeepSeek V4.1 Flash on a dedicated endpoint, called
through an OpenAI-compatible chat interface with reasoning effort set to its
maximum. A turn could use up to 64 rounds of tool calls. Each episode had 4,096
surrogate evaluations and 4 CFD queries in the comparison, or 32 CFD queries in
the extension. Reflection ran after every returned CFD result and carried a
summary of its running log capped at 4,000 characters. Planning compared four
plans of two steps each and could plan again at most three times per decision.

Each workspace also held the SuperWing records, in `data/superwing/`, and the
files in `workspace/source/`. Those files are the copies the agents read, kept
as they were. The last section of `CONDITIONS_README.md` says the complete run
records were not public and could not be published without the SuperWing
authors' permission. That was true when the agents ran. The authors have since
given that permission, and the records are in `data/superwing/`.

Each turn began with a message from the campaign manager, and the chat loop
added a short fixed system prompt. Both are quoted in
`prompts/manager_messages.md`. The code that wrote these, launched agent
sessions in isolated workspaces, relayed model calls, and sent CFD jobs to cloud
and cluster machines only works on those machines and is not included.

