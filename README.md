# Taskwarrior Roll + Crunch

Two dependency-free Taskwarrior hooks for keeping linked work scheduled:

- **Roll** moves dependent due dates when their predecessor moves or finishes.
- **Crunch** reports scheduling pressure without changing task dates.
- Fixed checkpoints and finish-line milestones keep important dates stable.
- Project capacity settings support informational longest dotted-prefix lookup.

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

The installer copies the hooks into `$HOME/.task/hooks`. It does not need
administrator access and refuses to replace a hook with the same filename.
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
task diagnostics
ls -l "${TASKDATA:-$HOME/.task}/hooks/on-exit-roll.py" \
      "${TASKDATA:-$HOME/.task}/hooks/on-modify.crunch.py" \
      "${TASKDATA:-$HOME/.task}/hooks/on-add.crunch.py"
```

The first command should finish without a configuration error. Under `Hooks`,
diagnostics should list all three files as active. The final command should
also list all three; `on-add.crunch.py` should point to
`on-modify.crunch.py`.

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

To uninstall, remove the three installed hook paths and the `include` line you
added to your Taskwarrior config. Removing hooks does not delete task data.

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

Changing `remaining` does not move due dates. Roll schedules from
`roll_offset`, which is calendar spacing rather than estimated work.

See [docs/crunch.md](docs/crunch.md) for the complete scoring rules.

### Roll

Point each rolling task at its predecessor and set the spacing with
`roll_offset`:

```sh
task TASK_ID modify roll:PREDECESSOR_UUID roll_offset:2d
task CHECKPOINT_ID modify roll:PREDECESSOR_UUID roll_offset:4d roll_fixed:checkpoint
task FINISH_ID modify roll:PREDECESSOR_UUID roll_offset:2d roll_fixed:finish-line
```

`checkpoint` preserves an intermediate date. `roll_fixed:finish-line` marks a
fixed finish-line milestone. A chain can be pictured as:

```text
A --2d--> B --4d--> C (checkpoint) --> D --> E (finish-line milestone)
```

Roll uses a predecessor's `due` time while it is active. On completion, it
applies `end + roll_offset` once and releases the ordinary child from the Roll
link. See [docs/roll.md](docs/roll.md) for field semantics, fixed dates, errors,
and cycle handling.

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
