# Crunch scoring

Crunch estimates how hard a task is to finish on time from Taskwarrior's
`duration`, `progress`, and built-in `due` fields.

## Formula

Convert `duration` to hours and treat `progress` as a percentage from 0 through
100. Remaining work is:

```text
remaining = duration * (1 - progress / 100)
```

When remaining work is zero, Crunch is unset and scoring stops.

The score is the sum of three parts:

1. Start bonus: `max(0, 1.5 * (1 - progress / 25))`.
2. Easy-task bonus, based on remaining hours:
   - `<= 0.25`: 2
   - `<= 0.5`: 1.5
   - `<= 1`: 1
   - `<= 2`: 0.5
   - otherwise: 0
3. Deadline pressure. Divide remaining hours by days until `due`:
   - overdue, or `>= 8` hours/day: 4
   - `>= 4` hours/day: 3
   - `>= 2` hours/day: 2
   - `>= 1` hour/day: 1
   - `>= 0.5` hour/day: 0.5
   - otherwise: 0

The resulting `crunch` value is:

- `CRITICAL` for score `>= 6`
- `HIGH` for score `>= 4`
- `MED` for score `>= 2`
- `LOW` for score `> 0`
- unset otherwise

Crunch scoring has no state-based or `+optional` reduction.
