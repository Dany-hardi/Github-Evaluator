# Measuring the time saving

The report's "% less manual review" is a **modelled workload figure**: it counts review items against a baseline of "every criterion of every submission checked by hand". It says nothing about minutes. This page is a protocol you can run in an afternoon to get a real, if small, measurement. Markbook does not ship a measured result, and none is claimed anywhere.

## What you will measure

Two arms on the same submissions, graded by a human:

- **Manual arm**: full checklist, no tool, stopwatch.
- **Queue arm**: `markbook grade`, then work through `markbook review` (CLI or web UI).

The outcome is human minutes per submission. Grade agreement between arms (do both arms give the same marks?) is worth recording too: a faster process that grades differently has not saved anything.

## Protocol

1. **Same cohort, same rubric.** Use 10 to 30 real (or realistic) submissions and the spec you would really use. Fewer than 5 is anecdote; `stats` will say so.
2. **Counterbalance.** Split the submissions into two matched halves, A and B (shuffle, or alternate by roster order; check the halves look similar in size and difficulty). Grader 1 does A by hand and B with the queue; if you have a second grader, they do the opposite. With one grader, say so in the report.
3. **Separate the arms in time** (a day, or at least a long break) and do not reveal marks from one arm in the other. Grading a submission by hand *after* seeing the tool's output measures verification, not grading.
4. **Fatigue and learning.** Graders speed up as they learn a rubric and slow down when tired. Counterbalancing spreads this across arms; it does not remove it. Do a few warm-up submissions that are not counted. Take breaks on a schedule, in both arms.
5. **What to log.**
   - Manual arm: seconds per submission from first opening it to final mark written down, excluding breaks. Write them to a CSV:

     ```
     submission_id,seconds
     alice,412
     bob,365
     ```
     The ids must be the run's submission ids (`markbook review RUN --all --json`).
   - Queue arm: nothing extra. The audit log (`overrides.json`) and, if you use the web UI, the view log (`views.jsonl`) are the record. Enter your name as the reviewer so decisions are attributed.
   - Also note: grader, date, which half, rubric version (the spec hash is in `run.json`), and any interruptions.
6. **Run `stats`:**

   ```
   markbook stats RUN                       # review effort from the audit log
   markbook stats RUN --baseline manual.csv # plus the by-hand comparison
   markbook stats RUN --idle-minutes 3 --json
   ```

## What `stats` computes, and how it can mislead

- **Active review time** is the sum of gaps between consecutive decisions by one reviewer that are at most the idle cutoff (default 5 min). Longer gaps are reported as breaks and excluded.
- With `views.jsonl` (written when a submission page is opened in the web UI), the time from opening a submission to its decisions is also counted, so the lead-in to the first decision is captured. Without views, the first decision of each stretch has no measurable lead-in.
- Either way the figure is a **lower bound**: reading time before the first event is missed. Using the same cutoff logic on manual timing would be fairer than a stopwatch; if your stopwatch includes reading and the queue arm's log does not, the comparison favours the tool. Treat the reported reduction as an upper bound.
- Submissions the queue never asked you to touch count as 0 seconds. That is the point of the tool, but it also assumes you trust the automated result without looking. Spot-check a sample and record disagreements.
- AI-assisted share and accept-unchanged rate describe behaviour, not quality. A high accept-unchanged rate can mean good suggestions or reviewer anchoring; compare against a no-AI half before concluding anything.
- The comparison is restricted to submissions present in both the CSV and the run, and is labelled "measured, N submissions, 1 grader". No p-values or confidence intervals are produced: with one grader and a handful of submissions there is nothing sound to compute them from.

## What not to conclude

- Not "Markbook saves X% of your time" for other courses, rubrics, graders or class sizes. Your rubric's mix of automatable and manual criteria drives everything.
- Not that the modelled review-load percentage equals a time saving. Report both side by side, as `stats` does, and let the gap speak.
- Not anything about grading *quality* from time alone.
- Not anything from a single run that finished while items were still in the queue.

## How to report it

State: number of submissions, number of graders, how halves were formed, order, rubric, idle cutoff, whether views were recorded, measured manual total/median, measured queue total/median, the ratio, the modelled figure, the cautions `stats` printed, and any grade disagreements between arms. Include the `stats --json` output and the CSV so others can recompute it.
