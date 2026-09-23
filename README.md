# Taskwarrior Roll/Rock + Crunch

![Taskwarrior Roll/Rock + Crunch](assets/github-banner.png)

Two dependency-free Taskwarrior hooks for keeping linked work scheduled, plus
Rock, Roll's companion planning command:

Release notes: [CHANGELOG.md](CHANGELOG.md)

- **Roll** moves dependent due dates when their predecessor moves or finishes.
- **Crunch** reports scheduling pressure without changing task dates.
- **Rock** plans backward through a Roll chain from a fixed finish-line deadline.
- **Phoenix** creates one follow-up task after a task is completed.
- Fixed checkpoints and finish-line milestones keep important dates stable.

## Requirements

- Python 3.10+
- Taskwarrior
- Git
- A POSIX shell (macOS, Linux, or WSL)

No Python packages are required.

## Install

These commands install both Roll and Crunch. Open Terminal and run:

```sh
git clone https://github.com/ylub/tw-roll-crunch.git
cd tw-roll-crunch
sh ./install.sh
```

The installer copies the hooks into `$HOME/.task/hooks` and installs
`task_roll_help` in `$HOME/.local/bin`. It does not need administrator access and
refuses to replace a hook or command with the same filename. Make sure
`$HOME/.local/bin` is in `PATH`.
If Taskwarrior uses a different data directory, provide its full path:

```sh
export TASKDATA=/path/to/task-data
sh ./install.sh
```

Next, while still inside the cloned `tw-roll-crunch` directory, connect the
included settings to Taskwarrior:

```sh
printf '\ninclude %s/config/taskrc.example\n' "$PWD" >> "$HOME/.taskrc"
```

Run that command once. If you use a custom `TASKRC` file, replace
`"$HOME/.taskrc"` with its path. Keep the cloned directory at this location;
the `include` line points to its `config/taskrc.example` file.

Verify the configuration and installed hooks:

```sh
task rc.hooks=0 show >/dev/null
task chain >/dev/null
task diagnostics
ls -l "${TASKDATA:-$HOME/.task}/hooks/on-exit-roll.py" \
      "${TASKDATA:-$HOME/.task}/hooks/on-modify.crunch.py" \
      "${TASKDATA:-$HOME/.task}/hooks/on-add.crunch.py"
```

The first two commands should finish without an error. Under `Hooks`,
diagnostics should list all three files as active. The final command should
also list all three; `on-add.crunch.py` should point to `on-modify.crunch.py`.

### Manual install

Roll and Crunch can be installed independently.

First choose the hook directory and create it:

```sh
HOOK_DIR="${TASKDATA:-$HOME/.task}/hooks"
mkdir -p "$HOOK_DIR"
```

Stop and back up or remove any same-named hook already in that directory before
running the manual commands.

Roll only:

```sh
install -m 0755 hooks/roll.py "$HOOK_DIR/on-exit-roll.py"
install -m 0644 roll_calendar.py "${TASKDATA:-$HOME/.task}/roll_calendar.py"
mkdir -p "$HOME/.local/bin"
install -m 0755 task_roll_help "$HOME/.local/bin/task_roll_help"
install -m 0644 roll_calendar.py "$HOME/.local/bin/roll_calendar.py"
install -m 0755 task_rock "$HOME/.local/bin/task_rock"
install -m 0755 task_chain "$HOME/.local/bin/task_chain"
install -m 0755 task_chains "$HOME/.local/bin/task_chains"
```

Crunch only:

```sh
install -m 0755 hooks/crunch.py "$HOOK_DIR/on-modify.crunch.py"
ln -s on-modify.crunch.py "$HOOK_DIR/on-add.crunch.py"
```

Then add the supplied configuration to your Taskwarrior config as shown in the
main installation steps. Taskwarrior normally uses `~/.taskrc`; `TASKRC` can
select another file. The supported `include` syntax is documented in the
[Taskwarrior configuration guide](https://taskwarrior.org/docs/configuration/).

To uninstall, remove the three installed hook paths, the two installed
`roll_calendar.py` copies, `$HOME/.local/bin/task_roll_help`,
`$HOME/.local/bin/task_rock`, `$HOME/.local/bin/task_chain`,
`$HOME/.local/bin/task_chains`, and the `include` line you added to your
Taskwarrior config. Removing hooks does not delete task data.

For an existing installation, `install.sh` refuses to overwrite its files.
Back up the installed Roll hook and commands outside the hooks directory,
then use the manual Roll install commands above to replace them. Leave your
personal calendar file in place.

## Usage

### Crunch

Set `remaining` to your current estimate of work left. Update it after each
session. `progress` is an optional, manually maintained percentage:

```sh
task add "Long research task" due:2026-10-15 remaining:2d progress:0
task TASK_ID modify remaining:18h progress:25
```

Crunch uses `remaining` directly for easy-task and deadline pressure. It does
not reduce that estimate by `progress`; `progress` only affects the start
bonus. Adding or modifying a task recalculates its Crunch level.

Run `task TASK_ID info` and look for the `Crunch` field: `LOW`, `MED`, `HIGH`,
or `CRITICAL`. A task with no `remaining` estimate has no Crunch level.

Changing `remaining` does not move a standalone task's due date. In a Roll
chain with project capacity, it can change linked tasks' due dates; see below.

See [docs/crunch.md](docs/crunch.md) for the complete scoring rules.

### Roll

Show the field guide and copyable examples:

```sh
task roll help
```

To chain existing tasks, select a project and save its `UUID:description` lines.
Put the lines in the order you want the tasks to follow, then preview:

```sh
task project:YOUR_PROJECT _zshuuids > chain.txt
task roll chain chain.txt --start 2026-09-22 --finish 2026-10-07
```

Replace `YOUR_PROJECT`, check the file order, and add `--apply` only after the
preview looks right. Without `--remaining`, Chain preserves each task's
estimate and lists tasks missing one. Use `--remaining 40m` only when every
selected task should get that estimate.

Without a calendar, `--start` and `--finish` must be weekdays. Chain's initial
dates use weekdays, but later Roll updates can place due dates on weekends.

Set weekly capacity for a project in `~/.taskrc`; dotted child projects inherit
their nearest parent value:

```ini
roll.capacity.posek=25
roll.capacity.other-project=12
```

`task chain` adds `CAPACITY` for each Rock finish-line path. It is the
available project hours from today through its deadline, minus the path's total
`remaining` estimate. Without a calendar, Saturday and Sunday count.
`—` means the project has no capacity setting or a path task lacks `remaining`.
The `CAPACITY` column is a feasibility signal; it does not change `R_mark`.

Without a calendar, for every linked task with `remaining` and a matching
capacity, Roll calculates that task's moving `roll_offset` as
`remaining hours / weekly capacity * 7 days`. With a calendar, it uses six
available days per week.
Roll rounds to 30-minute slots and refreshes the
offset and downstream flexible dates after any task change.

- Increase a linked task's `remaining`: its due date and later flexible dates
  can move later.
- Decrease it: those dates can move earlier. Small changes may round to the
  same offset.
- Earlier tasks do not move. Changing the root's `remaining` does not move its
  due date.

#### Days off and half days

From this repository, copy [the 5787 Diaspora calendar](config/calendar-5787.taskrc)
to a personal file:

```sh
cp -i config/calendar-5787.taskrc "${TASKDATA:-$HOME/.task}/roll-calendar.taskrc"
```

Then add this line **once** to your Taskwarrior configuration (`${TASKRC:-$HOME/.taskrc}`),
using the full path to your copy:

```ini
include /absolute/path/to/roll-calendar.taskrc
```

The copy is yours to edit; upgrades do not replace it. The example marks Yom
Tov and Tisha B'Av off, and Erev Yom Tov at half capacity. Add vacation dates
in the same file:

```ini
roll.calendar.2026-12-20..2026-12-27=0
roll.calendar.2027-01-05=0.5
```

Ranges include both endpoints. Values are `0` (off), `0.5` (available until
12:00:00 local time), and `1` (available all day). Conflicting or invalid
entries block calendar scheduling until fixed. Dates use your computer's local
timezone. Once the calendar is
included, Sunday through Friday are available by default and Saturday is off;
an explicit entry can override Saturday. Weekly project hours are spread over
six normal workdays. `roll_offset:P1D` then means one available day, including
when `roll_manual:yes` keeps that amount fixed. Chain's initial flexible due
times on half days end by 12:00 local time.

Roll updates existing flexible chain dates on the next Taskwarrior run, even if
you only changed the calendar file. Rock, Chain, and `CAPACITY` use
the same calendar. A Rock finish-line may stay fixed on a day off; work is
planned before it. `task roll view`, `task rock view`, and `task chain NUMBER`
show calendar breaks longer than one day, omitting routine Saturdays. Short
appointments can be handled by adjusting the affected task manually.
When Roll moves a linked due date across an explicit off or half day, or you
manually set a chain task due during that time, it prints a subdued cyan calendar
notice. Routine Saturdays do not trigger a notice. Standalone tasks keep their
due dates and receive no calendar notice.

For a once-daily or otherwise intentional gap, preserve your own offset with:

```sh
task TASK_ID modify roll_manual:yes roll_offset:P1D
```

Roll still moves that task's due date from its predecessor. Clear the override
with `task TASK_ID modify roll_manual:` to resume capacity auto-spacing.

#### Repeat once with Phoenix

For a task that should appear once more after it is completed, use Phoenix:

```sh
task TASK_ID modify phoenix:PT90M
```

Completing `Laundry` creates one same-named `Laundry` task due 90 minutes
later. The new task keeps project and tags, but not `phoenix`, Roll fields, or
tracking session data.

`task roll chain` creates a flexible chain with no fixed finish-line. Its last
task is a leaf whose due date can move; `--finish` sets only its initial date.
Use `task rock chain` when the last task must stay fixed at `--finish`.

Show numbered active Roll paths, then inspect one path:

```sh
task chain
task chain 2
```

`task chain` shows root, task count, start, finish or leaf, deadline, capacity,
and status. On a flexible path, `DEADLINE` is the leaf's current rolling due
date, not a fixed finish-line. `task chain NUMBER` shows every task in that
path. Capacity is `—` for flexible paths. `task chains view` remains the
original compact chain summary without numbering or capacity.

Show flexible link details and offsets:

```sh
task roll show
```

`task roll view` remains an alias for `task roll show`. The `R` column in either
command shows only flexible Roll links:
`󰍃 +GAP` means the task follows its predecessor by that moving gap. Use
`task chain NUMBER` to see fixed checkpoints and finish-lines in the path.

Point each rolling task at its predecessor and set the spacing with
`roll_offset`:

> **Warning:** Changing a predecessor's due date or completing it can update
> the due dates of ordinary linked children.

```sh
task TASK_ID modify roll:PREDECESSOR_UUID roll_offset:2d
task CHECKPOINT_ID modify roll:PREDECESSOR_UUID roll_offset:4d roll_fixed:checkpoint
task FINISH_ID modify roll:PREDECESSOR_UUID roll_offset:2d roll_fixed:finish-line
```

`checkpoint` preserves an intermediate date. An ordinary Roll chain ends at a
flexible leaf, not a fixed finish-line:

```text
A --2d--> B --4d--> C (checkpoint) --> D --> E (flexible leaf)
```

Set `roll_fixed:finish-line` on E only when E has a firm deadline for Rock.

#### Pause a chain at a checkpoint

Suppose task 38 is the first task you expect to finish after a break. Set its
planned due date and checkpoint together:

```sh
task 38 modify due:2026-10-06 roll_fixed:checkpoint
```

Replace the ID and date with your task and planned completion date.

- Changing `due` alone is not enough: Roll can recalculate a flexible task's
  date from its predecessor.
- Task 38 keeps the fixed date even if earlier tasks move. Tasks after 38 roll
  from that date. If 38 will finish before the break, put the checkpoint on the
  next task instead.
- A checkpoint is not a Rock finish-line or a day-off calendar entry.
- Use `roll_fixed:vacation` or `roll_fixed:off` for the same boundary with a
  distinct marker. Set `wait:` separately if you want the task hidden until a date.

Check the date and `󰩈 checkpoint` marker with `task chain NUMBER`.

Roll uses a predecessor's `due` time while it is active. On completion, it
applies the child's offset from `end` once (counting available time when a
calendar is included) and releases the ordinary child from the Roll link.
See [docs/roll.md](docs/roll.md) for field semantics, fixed dates, errors,
and cycle handling.

### Rock

Rock previews backward from a solid finish-line deadline by default. It shows
the root date that ordinary Roll should use to schedule the chain forward.

```sh
task rock FINISH_ID
task rock FINISH_ID --apply
```

`task rock FINISH_ID` is a dry run. `--apply` changes the printed root due date
only when the plan has no warnings; ordinary Roll then updates the chain.
Rock stops at checkpoints and refuses to apply when moving the root would also
roll another active branch.

`task chain` reads capacity fresh from `~/.taskrc` each time. Add or
change `roll.capacity.PROJECT=HOURS_PER_WEEK`, then rerun it; no task
modification or scheduler refresh is needed.

### Upgrading Roll displays

Roll v2 writes `r_mark` instead of `roll_mark`. The supplied config defines
both fields during the transition, and the new hook clears old marks as it
updates tasks. An existing `include` picks this up after updating the clone; if
you copied the UDA definitions instead, add `r_mark` before installing the new
hook. In custom reports, replace `roll_mark` with `r_mark` and change the
corresponding label to `R`.

## Upgrading from `duration`

Older Crunch setups used a `duration` UDA for the work estimate. Back up your
tasks and migrate those values before removing that UDA:

```sh
task rc.hooks=0 export > tasks-before-remaining.json
task TASK_ID modify remaining:OLD_VALUE duration:
```

Repeat the second command for each task that has `duration`, using its existing
value as `OLD_VALUE`. Keep both UDA definitions during the migration. Then
update the Crunch hook, reports, and configuration to use `remaining`.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## Credits and project relationship

This repository was built out with assistance from
[OpenAI Codex](https://openai.com/codex/), based on an existing Roll + Crunch
workflow and requirements supplied by [@ylub](https://github.com/ylub).

This is an independent third-party extension for
[Taskwarrior](https://taskwarrior.org/)
([source code](https://github.com/GothenburgBitFactory/taskwarrior)).
It was not created, maintained, sponsored, or endorsed by Taskwarrior or
Gothenburg Bit Factory. OpenAI and Codex did not create Taskwarrior.

## License

MIT. See [LICENSE](LICENSE).
