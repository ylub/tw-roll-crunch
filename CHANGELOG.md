# Changelog

All notable changes are recorded here for each release.

## [Unreleased]

## [0.3.0] - 2026-09-20

### Added

- List-driven flexible Roll chains and fixed Rock chains.
- `task chains view` for complete active Roll paths.
- Per-project weekly capacity and deadline capacity cushion in `task rock view`.

### Changed

- Flexible Roll offsets derive from `remaining` and project capacity, rounded to
  30-minute slots across seven calendar days.
- `task roll view` shows flexible links only; `task rock view` shows hard
  finish-lines only.
- Chain input preserves individual `remaining` estimates and identifies every
  missing estimate before applying changes.
