# Raman/Wavelength calibration hardening (schema v2 / catalog v3)

## Motivation

The original `ConfigurationCatalog` (see `work_configuration.md`) let one physical slot
(hardware + grating + centre + ROI) hold exactly one active calibration, regardless of its
`calibration.unit`. In practice this meant:

- `get_x_axis()` silently converted a loaded calibration's polynomial between nm and cm⁻¹
  whenever the Wavelength/Raman display toggle disagreed with `calib_unit`, using whatever
  excitation wavelength happened to be in the spinbox at that moment — not necessarily the
  laser the calibration was taken with.
- Toggling the display mode, or editing the excitation wavelength, never invalidated an
  active calibration; the silent conversion above papered over the mismatch instead.
- Saving a Raman calibration at a slot that already had an active Wavelength calibration (or
  vice versa) silently archived the other one, because `register_configuration()` only ever
  tracked one "active" configuration per slot.
- A saved configuration record duplicated the excitation wavelength and display mode in two
  places (`display.*` and `calibration.*`), and loading preferred the `display.*` copy —
  overwriting the operator's live excitation-wavelength spinbox with whatever was saved, with
  no check that today's laser is actually the one the calibration was taken with.
- The pressure calculator picked its sensor list from the display toggle alone, so a stale
  pixel axis dressed up as "Raman shift" could still produce a number.

This change makes `axis_kind` (Wavelength vs. Raman shift, at a given excitation wavelength)
a hard boundary that is never silently crossed, and lets one slot hold an active Wavelength
calibration and one or more active Raman calibrations (one per excitation laser)
simultaneously.

## Data model: calibration profiles

```
slots (physical condition: hardware + grating + centre + ROI)
  └─ calibration_profiles (slot_id, axis_kind, excitation_wavelength_key)
       └─ configurations (immutable versions; calibration_profile_id FK)
```

`calibration_profiles` is new (catalog schema v3, `CATALOG_SCHEMA_VERSION`). A profile is
identified by `(slot_id, axis_kind, excitation_wavelength_key)`, where
`excitation_wavelength_key(nm) = round(nm * 1000)` (0.001 nm resolution; a fixed sentinel of
`-1` for `axis_kind == "wavelength"`, which never has an excitation wavelength). `slots`
keeps meaning "physical condition" only — `_signature()` is unchanged — and
`slots.active_configuration_id` is deprecated in code (no longer read or written anywhere;
left in place as an unused legacy column rather than risking a `DROP COLUMN` against an
unknown SQLite version). "Is this configuration active" is answered only by
`calibration_profiles.active_configuration_id` from here on.

`register_configuration()` finds-or-creates the slot exactly as before, then finds-or-creates
the calibration profile for the draft's `(axis_kind, excitation_wavelength_key)` and archives
only that profile's previous active configuration — not the slot's other profiles. This is
the direct fix for the cross-unit archiving bug above.

Deletion now has three tiers (`configuration_catalog.py`, surfaced in
`src/ui/menu/configuration_manager_dialog.py`):
- `delete_configuration_version`: one archived (non-active-for-its-profile) version.
- `delete_profile` (new): one calibration profile's every version, leaving the slot and
  sibling profiles untouched.
- `delete_slot`: the entire physical condition — every profile, every version.

A configuration whose `calibration_profile_id` is `NULL` (a migration-skipped/damaged
record — see below) is protected by nothing and can always be deleted directly via
`delete_configuration_version`.

## Migration (v1 → v2 → v3)

`_migrate_catalog()` is staged (`if version < 2: ...; if version < 3: ...`) so a catalog
already upgraded to v2 by an earlier release doesn't re-run the v1→v2 rebuild, and a
still-v1 catalog runs both steps in one open. The v2→v3 step reads **every** configuration's
full history (not just each slot's current active record — the bug above means an older,
still-valid calibration of a different axis_kind/laser can already be archived behind a
newer one of a different kind), groups by `(slot_id, axis_kind, excitation_wavelength_key)`
derived from each record's own `calibration.unit`/`calibration.excitation_wavelength_nm`, and
reconstructs one profile per group with the most recent member active. A damaged/unreadable
file is skipped (same tolerance as the pre-existing v1→v2 loop) but left in `configurations`
with `calibration_profile_id` still `NULL` — never silently dropped — so it still surfaces
via `list_all()`'s management view (`migration_error: true`) instead of disappearing.

Note the `calibration_profiles` table and `configurations.calibration_profile_id` column are
also part of the base `executescript` DDL, not only the migration path: a brand-new database
writes `schema_version` as `CATALOG_SCHEMA_VERSION` immediately (before `_migrate_catalog`
ever runs), so the fresh-database shape has to be correct on its own.

Existing v1 JSON records on disk are **not** rewritten. `get_configuration()` normalizes them
to the v2 shape in memory on every read: folds away the redundant `display.*` block, injects
the catalog's `calibration_profile_id`, and defaults `calibration.reference_kind` from the
unit. A v2 record's own embedded `calibration_profile_id` is cross-checked against the
catalog's for that row (integrity check, same spirit as the existing sha256 check); a
`schema_version` newer than this code understands is rejected outright rather than guessed at.

## Invalidation, not silent conversion

`deactivate_axis_calibration(reason)` (`file_io_mixin.py`) is the shared building block:
clears `calib_coeffs`/`calib_unit`/`calib_laser_wl` and falls back to a pixel axis, but —
unlike `clear_active_configuration()` — leaves `positioned_configuration_id` alone, since a
unit toggle or excitation edit doesn't move the grating/centre/ROI.

- `on_spec_mode_changed()`: if the new display mode disagrees with `calib_unit`, deactivate.
- `on_exc_wl_changed()`: if the new value's `excitation_wavelength_key` disagrees with
  `calib_laser_wl`'s, deactivate.
- `_prepare_configuration_for_loading()`: when `axis_mode == "calibrated"` and the record is
  a Raman calibration, compares the record's excitation wavelength against the current
  spinbox *before any widget is mutated*; on mismatch raises
  `ConfigurationCompatibilityError` instead of silently overwriting the spinbox.
  `axis_mode == "pixel"` (API-only; positions hardware without applying calibration) skips
  this check entirely and never touches the excitation spinbox or the display toggle —
  `skip_move` (whether the spectrometer physically moves) and `axis_mode` are independent
  knobs and must not be conflated.
- `get_x_axis()`: the nm↔cm⁻¹ conversion branch is deleted outright. Once the above hold,
  `calib_unit` matching the display toggle is an invariant whenever `calib_coeffs is not
  None`, so the function is just a polynomial evaluation. Ocean Optics' native-wavelength
  fallback is a deliberate, narrower exception: it derives a live axis from the *current*
  spinbox on every call (nothing is frozen at calibration time), so there is no "wrong
  laser" state to detect there.
- Pressure calculation is gated the same way: `open_pressure_calculator`/
  `sync_pressure_calculator_mode` and `update_display()`'s pressure hookup all check
  `public_axis_kind() != "pixel"` before computing/forwarding a peak position. The API's
  `POST /acquire/pressure` already rejected `axis_mode != "calibrated"`; it now also rejects
  a sensor/axis **unit** mismatch (Wavelength calibration active + a Raman-shift sensor
  requested, or vice versa) even when some calibration is active.

## API surface

- `GET /configurations` summaries gained `axis_kind`, `excitation_wavelength_nm`,
  `calibration_profile_id`, `profile_count_for_slot` — no response-model change needed since
  `items` is untyped.
- `POST /configurations/resolve` accepts either a bare `slot_id` (resolves only if the slot
  currently has exactly one active profile — the common case, unchanged) or
  `{slot_id, axis_kind, excitation_wavelength_nm}` to name a profile explicitly. A bare
  `slot_id` with more than one active profile raises the new
  `AmbiguousConfigurationProfileError` → `409 {"code": "ambiguous_configuration_profile"}`.
- Applying a specific `configuration_id` was already unambiguous (one immutable record always
  carries exactly one axis_kind) and needed no change.

## Post-implementation review fixes

A code review of the initial implementation found four issues, all fixed:

- **Fresh-DB DDL crash on real pre-existing databases.** `idx_slots_hardware_identity`
  was already created after `_migrate_catalog()` (since `spectrometer_model`/
  `camera_model` only exist post-migration on an old database), but
  `idx_configurations_profile_created` was still being created *inside* the initial
  `executescript`, unconditionally, before migration ran. `CREATE TABLE IF NOT EXISTS`
  is a no-op against an already-existing `configurations` table, so a real old
  database (not just a fresh v3 database with its `schema_version` string manually
  rolled back, which is all the original migration tests exercised) would hit
  `OperationalError: no such column: calibration_profile_id` before ever reaching
  `_migrate_v2_to_v3`. Fixed by moving that index's creation to after
  `_migrate_catalog()`, alongside `idx_slots_hardware_identity`. Covered by
  `test_real_v1_schema_database_opens_and_migrates_without_crashing`/
  `test_real_v2_schema_database_opens_and_migrates_without_crashing`, which build the
  catalog from the actual historical DDL (and a real v1 JSON record) rather than
  downgrading an already-current database's version number.
- **Stale calibration surviving a failed configuration-load move.**
  `_prepare_configuration_for_loading()` switches the ROI/display-mode/excitation
  widgets to the new record's values *before* the physical move is attempted; if the
  move then fails (`on_spectrometer_moved()`'s `success is False` branch), the old
  `calib_coeffs`/`calib_unit` were left untouched -- so a display already showing
  "Raman shift" could go on reporting values computed from a stale Wavelength
  polynomial. Fixed by calling `clear_active_configuration()` in that branch too,
  matching the cancelled-move branch just above it and the plain successful-Apply
  path.
- **`calibration_profiles.active_configuration_id`/`configurations.calibration_profile_id`
  had no FK.** Added `FOREIGN KEY(calibration_profile_id) REFERENCES
  calibration_profiles(calibration_profile_id)` (nullable, satisfied trivially for
  migration-skipped/orphaned rows) to both the fresh DDL and the `_migrate_v2_to_v3`
  `ALTER TABLE`, verified against the insertion order already used by
  `register_configuration()`/`_migrate_v2_to_v3` (the profile row always exists
  before a configuration row references it).
- **`POST /configurations/resolve`'s dict selector accepted incomplete/contradictory
  input.** `{axis_kind: "raman_shift"}` without an excitation wavelength, or an
  excitation wavelength with no `axis_kind` at all, used to be accepted and silently
  mishandled downstream (the excitation value ignored, or the entry resolved as if it
  were a bare `slot_id`). Added a correlated validator on `SlotResolutionRequest`
  requiring a finite, positive `excitation_wavelength_nm` if and only if
  `axis_kind == "raman_shift"`.

## Second review round

A follow-up review found four more issues, all fixed:

- **`apply_calibration()` bypassed the axis-unit invariant entirely.** Unlike
  `_prepare_configuration_for_loading()`, the Calibration window's "Save and apply"
  (its own `radio_unit_raman`/`radio_unit_wl` is only copied from the main window once,
  when the dialog opens -- it's an independent toggle after that) and the deprecated
  inline `POST /calibration` (never touched the toggle at all) could both set
  `calib_unit` while the main window's Wavelength/Raman display toggle stayed wherever
  it already was, reproducing exactly the invariant `get_x_axis()`'s own comment
  assumes can't happen. Fixed by extracting the toggle-sync logic
  `_prepare_configuration_for_loading()` already had into `_sync_display_mode_to_unit()`
  and calling it from `apply_calibration()` itself -- the one choke point every caller
  (loaded configuration, Calibration window, inline API) goes through.
- **Move failure/cancellation cleared state but left stale plot/pressure values on
  screen.** `clear_active_configuration()` only updated labels/state; unlike
  `deactivate_axis_calibration()`, it never resynced the fit range, repainted the plot,
  or cleared the Pressure Window's stale peak. Fixed by extracting a shared
  `_refresh_after_axis_change()` tail used by both methods.
- **Deprecated inline `POST /calibration` accepted non-finite coefficients and invalid
  excitation wavelengths.** This route never goes through configuration_catalog's own
  finite/positive validation. Added `Field(allow_inf_nan=False)` to `c0`/`c1`/`c2` and
  `Field(gt=0, allow_inf_nan=False)` to `laser_wavelength_nm` directly on
  `CalibrationRequest`.
- **Profile/slot deletion only checked the selected row's own id against the loaded
  configuration.** Deleting a whole profile/slot also removes every other version
  under it -- if one of those (not the checked row) is what the GUI has loaded or is
  positioned at (e.g. an archived version applied remotely via the API), the dialog
  never noticed and the GUI kept using a calibration that no longer exists on disk.
  Fixed by checking `active_configuration_id`/`positioned_configuration_id` against
  each `delete_*()` call's actual `deleted_configuration_ids` (or
  `configuration_id` for a single-version delete), not just the checked summary's id.

## Third review round

A second follow-up review found three more issues, all fixed:

- **`_sync_display_mode_to_unit()` only flipped the radio buttons, missing every
  other side effect `on_spec_mode_changed()` applies for a user-driven toggle:
  `spin_exc_wl`'s enabled state, the displayed centre value (still nm<->cm-1, just
  recomputed from `physical_center_wl` instead of the calibration's own target), the
  Apply button's state, fit-range-dependent UI, and the Pressure Window's sensor
  list.** Reproduced exactly: applying a Raman calibration at 532 nm excitation over
  a 690 nm physical centre left `spin_centre_wl` showing 690 instead of the correct
  ~4304.24 cm⁻¹, and an open Pressure Window kept its old-unit sensor list. Fixed by
  extracting `_sync_controls_to_display_mode()` (`SpectrometerControlMixin`) as the
  single shared tail both `on_spec_mode_changed()` and `_sync_display_mode_to_unit()`
  call, so a display-mode change can never happen through one path with the other's
  side effects missing.
- **The deprecated inline API could silently overwrite the current excitation
  wavelength instead of erroring on mismatch.** A loaded configuration already
  rejects a laser mismatch before mutating anything
  (`_prepare_configuration_for_loading()`), and the Calibration window's "Save and
  apply" reads `calib_laser_wl` directly from the live `spin_exc_wl` value (so it
  always already agrees) -- but `apply_calibration()` itself enforced nothing, so the
  inline API (the only caller supplying an independent `calib_laser_wl`) could apply
  a 532 nm Raman calibration while the GUI stayed at 633 nm, silently overwriting the
  spinbox rather than erroring. Fixed by comparing `spin_exc_wl.value()` against
  `calib_laser_wl` (via `excitation_wavelength_key()`) at the top of
  `apply_calibration()` itself, raising `ConfigurationCompatibilityError` (mapped to
  `409` in `post_calibration`, same as every other configuration-compatibility
  rejection) before any state changes; both other callers pass through unaffected
  since they already agree by construction.
- **Migration had no semantic validation of `calibration.unit`/coefficients/excitation
  wavelength, and one gap could crash catalog opening entirely.**
  `_axis_kind_from_unit()` treats anything other than `"Raman shift"` as Wavelength,
  so a readable-but-corrupted `unit` value used to be silently resurrected as an
  active Wavelength profile. Separately, `excitation_wavelength_key()`'s
  `round(nm * 1000)` raises `OverflowError` for `Infinity`/`NaN` (which
  `json.loads` accepts as an extension) -- uncaught, this aborted the *entire*
  catalog open over one damaged record instead of skipping just that record. Fixed
  by validating `unit` against the two known literal values, checking `c0`/`c1`/`c2`
  and `excitation_wavelength_nm` are finite with `math.isfinite()` before ever
  calling `excitation_wavelength_key()`, and adding `OverflowError` to the migration
  loop's caught-exceptions tuple as defense in depth.

## Deliberately out of scope for this pass

- A named laser-profile registry (`profile_id` beyond the bare excitation-wavelength key).
  Excitation identity is compared purely as a 0.001 nm-resolution number.
- The rich `calibration.reference.assignments`/`fit_quality` provenance block — would need
  new plumbing through `calibration_ui.py` that nothing currently needs.
- `validated_pixel_range` monotonicity/duplicate-pixel checks at save time.

**Update (2026-07-24):** the `raman_standard` reference kind above is no longer out of scope.
`calibration_ui.py`'s `save_and_apply()` now derives `calibration.reference_kind` and
`calibration.standards` (the catalogue standard(s) actually behind the checked/assigned peaks
that fed `calibrate()`, mirroring its own check+assignment condition) instead of leaving them
as a tolerated-but-unproduced schema slot. `raman_standard` fires whenever any used standard's
`quantity` is `raman_shift_cm1` (a Raman-active material measured directly, e.g. the ASTM/NMIJ
catalogues), as opposed to `emission_lines_with_excitation` (Ne/Ar/Hg lamp lines converted via
the excitation wavelength). Both fields are also defaulted (from `unit` alone, `standards: []`)
for pre-existing records that predate this — in `ConfigurationCatalog.register_configuration()`
for a draft missing them outright, and in `_normalize_record()` for a record already on disk —
so every record served by `get_configuration()` carries both keys regardless of when it was
written. See `docs-site/docs/data-formats/configuration.md` for the on-disk shape.
