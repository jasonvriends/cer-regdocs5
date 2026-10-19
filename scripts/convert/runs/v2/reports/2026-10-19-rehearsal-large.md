# Rehearsal B: the largest documents in full mode (2026-10-18/19)

`convert.py run --sample runs/v2/rehearsal-large` - 3 workers, full mode, checkpoints every 100 pages.
Relaunched once (the launcher hit its time limit); 2498585 and 4647200 resumed from their checkpoints.
Logs: `runs/v2/logs/rehearsal-large-2026-10-18.log`, `...-memory-2026-10-18.log` (one line a minute).

| doc | pages | words | time (this launch) | s/page | result |
|---|---:|---:|---:|---:|---|
| 4647200 | 986 | 355,277 | 4,543 s | 4.6 | ok |
| 2498585 (printed weather spreadsheet) | 11,563 | 3,088,804 | 19,811 s | 1.7 | ok |
| 4098218 (IR response, dense tables) | 2,759 | 7,123,709 | 26,689 s | 9.7 | ok |

3 converted, 0 failed, 26,722 s wall time.

## Memory

- During pages, a worker grows about 37-45 MB per 100 pages: 2498585 reached 11.1 GB at page 11,563.
- **Assembly scales with words, not pages.** 2498585 (3.1 M words) finished with no visible jump.
  4098218 (7.1 M words, the most of any document in REGDOCS) went from 12.7 GB to **34.2 GB** at
  assembly; all workers together peaked at **43.0 GB of 47 GB**.
- Only 7 documents exceed 2 M words and 4 exceed 3 M (4098218, 4724106, 2498585, 2498586); the next
  largest after 4098218 has 3.1 M.

## Decision for the full pass

The checkpoints and the GPU guard work; nothing failed. The one risk is a 7 M-word document assembling
while other workers are busy: 34 GB plus a 6-worker main lane does not fit in 47 GB. So documents over
2 M words (7) run last, one at a time, with nothing else running. No code change is needed for that;
it is a run-order rule.
