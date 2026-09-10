# Crunch scoring

Crunch estimates how hard a task is to finish on time from Taskwarrior's
`remaining`, `progress`, and built-in `due` fields.

## Fields

- `remaining`: current estimated work left, entered as a Taskwarrior duration
  such as `30m`, `18h`, or `2d`. Update it after each work session.
- `progress`: optional manual percentage from 0 through 100. It affects only
  the start bonus.
- `due`: optional deadline used to calculate required work per day.

```sh
task TASK_ID modify remaining:18h progress:25
```

`remaining` is the authoritative estimate. Crunch never derives it from
`progress`. Roll also ignores it; changing rolling due dates requires
`roll_offset`.

## Formula

Convert `remaining` to hours. Update it manually after work sessions. Treat
`progress` as a manually maintained percentage from 0 through 100; it affects
only the start bonus and does not reduce `remaining` again.

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
