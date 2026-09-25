# Agent prompts and task files

This folder holds what the three agent methods were told: the task every agent
received, the tool it used to query the surrogate and hand off a wing, and the
extra prompts used by the reflection and planning methods.

| Path | What it is |
| --- | --- |
| `workspace/TASK.md` | The task description at the top of every agent workspace |
| `workspace/campaign.py` | The tool every agent used to read the task, query the surrogate, check its budget, and hand off one wing for CFD |
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

## How the agents ran

All three methods used DeepSeek V4.1 Flash on a dedicated endpoint, called
through an OpenAI-compatible chat interface with reasoning effort set to its
maximum. A turn could use up to 64 rounds of tool calls. Each episode had 4,096
surrogate evaluations and 4 CFD queries in the comparison, or 32 CFD queries in
the extension. Reflection ran after every returned CFD result and carried a
summary of its running log capped at 4,000 characters. Planning compared four
plans of two steps each and could plan again at most three times per decision.

Each workspace also held the SuperWing records, a guide to the 38 design
variables, a guide to the flight conditions, and small scripts to load the data
and draw a wing. The SuperWing records are in `data/superwing/`. The two
guides and the helper scripts are not included here.

Each turn began with a message from the campaign manager, and the chat loop
added a short fixed system prompt. The code that wrote these, launched agent
sessions in isolated workspaces, relayed model calls, and sent CFD jobs to cloud
and cluster machines only works on those machines and is not included.

