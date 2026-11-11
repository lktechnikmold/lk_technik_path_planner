# Changelog

All notable changes to this plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Layer names are given as they appear in German (e.g. *Feldgrenzen* = field
boundaries, *Fahrspuren* = swaths/guidance tracks, *Punkthindernis* = point
obstacle, *Flaechenhindernis* = area obstacle, *Felder* = field catalogue).

## [2.3.0] - 2026-11-11

**QGIS 4**, **CNH CN1**, **track creation** and many improvements to import
and export.

### Added
- **QGIS 4 (Qt6)**: the plugin runs on QGIS 3 (from 3.16) and QGIS 4 from the
  same code base.
- **CNH CN1** (Case IH/Steyr, New Holland): export and import. New terminal
  entry "CN1 – CNH"; Case/Steyr AFS Pro 700 and New Holland Intelli View 4 now
  export CN1. Import from the `.cn1` folder, via `index.vy1` or directly from
  the ZIP file. CN1 supports only one boundary per field (several boundaries
  are merged, a hint is shown in the dialog), holes are written as impassable
  obstacles, names are shortened to 18 characters.
- **Track creation tab** ("Spurenerstellung"): creates guidance tracks from the
  boundaries of the selected fields. The boundary is split at corners (also
  chamfered ones); 2 vertices = straight line, 3 or more = curve; pieces
  shorter than 15 m are dropped. Tracks lie exactly on the boundary and are
  shifted parallel inwards by the chosen distance without getting shorter.
  Where two boundaries of a field touch, an extra track is created along the
  shared border. Optional **name prefix** (e.g. "Grubber Gerade 1") and
  **contour segment**.
- **Holes in field boundaries and obstacles** for ISOXML and John Deere Gen4
  (import and export).
- **Name for area obstacles** (optional, max. 32 characters): ISOXML `PLN@B`,
  Gen4 `Flag@Name`, AgGPS `AreaFeature.shp`; read back on import. Existing
  layers get the field automatically.
- **Zoom to field** in the right-click menu of the export tree (covers all
  boundaries of the field).
- **Delete several fields at once** (multi-selection in the export tree).
- **Overwrite confirmation** on export and when importing into a target folder
  that already contains field data.
- **"Please wait" message** during import and export.
- **Contour segments** also for Fendt Vario Terminal INT 01, Valtra Smart
  Touch, Massey Ferguson Datatronic 5 and ISOXML v4.
- Terminals: LACOS LC:ONE, New Holland Intelli View 12 (both ISOXML v4).

### Changed
- Default terminal is now **ISOXML v4**.
- Terminal list cleaned up: series with the same format are merged into one
  entry (e.g. Trimble "CFX / FM / FMX / GFX / TMX", Raven "CR / Viper",
  Topcon "X / XD", John Deere "GS4 / GS5"); Case/Steyr "S-Fleet Pro" is now
  "Fleet Pro", Deutz "iMonitor".
- Fields are sorted alphabetically in the export tree and the field dropdown.
- Labels of field boundaries and tracks have a white buffer.
- Area obstacle columns in the order Feld | Name | befahrbar.
- "Densify curves" and "Extend curves" now also apply to the John Deere Gen4
  and AgGPS export.

### Fixed
- A second import into the same target folder doubled all data and added a
  second set of layers.
- John Deere Gen4: curves with 3–4 vertices were recognised as straight lines
  by the terminal; curves now have at least 5 vertices.
- Names (customer, farm, field, track, segment) are limited to 32 characters
  again; the "Segment" field stays optional.

### Internal
- Automated test suite (87 tests, synthetic data only) for QGIS 3 and QGIS 4,
  run before every push (`tests/run_tests.bat`, hook in `tools/hooks`).

## [2.2.0] - 2026-08-05

### Fixed
- QGIS plugin repository security scan: external XML files are parsed with
  `defusedxml` (bundled), protecting against XXE and "billion laughs".

## [2.1.1] - 2026-07-28

### Fixed
- ISOXML v3 import now also reads tracks stored as GGP/GPN (e.g. written by
  CNH terminals).
- "befahrbar"/"nicht befahrbar" (passable/not passable) is translated in
  English.
- Field boundary colours are assigned across all customers, so farms of
  different customers can be told apart.

## [2.1.0] - 2026-07-28

**Multilingual user interface (German/English)** and several stability fixes
around layer names and the field dropdown.

### Added
- **Language switch German/English**: dropdown at the top right of the Path
  Planner (above the logo). The choice is saved permanently (also across QGIS
  restarts).
- With English active, the layers in the Layers panel are really renamed:
  `Felder` → `Fields`, `Feldgrenzen` → `Boundaries`, `Fahrspuren` → `Swaths`,
  `Punkthindernis` → `PointFeature`, `Flaechenhindernis` → `AreaFeature`.
- Column aliases in the attribute table follow the language, e.g.
  `Flaeche` → `Area`, `befahrbar` → `Passable`. The `ID` column (field
  assignment) is now called `Feld` (German) or `Field` (English) instead of
  `ID`.
- All buttons, dialogs, tooltips and messages of the plugin are translated.
- Terminals: generic "ISOXML v3" and "ISOXML v4" entries, Case/Steyr S-Fleet Pro,
  CHC NAV, FJ Dynamics, Sveaverken Autosteer (retrofit systems, ISOXML v4).

### Changed
- Larger logo in the Path Planner and a rearranged header (language selection
  at the top right above the logo) for a tidier interface.
- The field dropdown (value relation for the ID column), styles, colours and
  the field catalogue synchronisation are now set up automatically when a
  **project is opened** – no longer only after the Path Planner has been
  opened manually once.

### Fixed
- Layer names can now be found reliably in every language: all internal layer
  lookups (style assignment, boundary colouring, Felder.csv automation,
  ISOXML/John Deere/AgGPS export) recognise both the German and the English
  layer name. This prevents a duplicate `Felder`/`Fields` catalogue layer when
  a new field is created; existing duplicates are cleaned up automatically.
- Missing field dropdown when a new field boundary was drawn directly after
  opening a project (without opening the Path Planner first).

## [2.0.1] - 2026-07-27

### Added
- John Deere Gen4 import: fields with several boundaries (each polygon becomes
  its own boundary with the same field ID; boundary name from the
  OperationalBoundary element).

### Changed
- New style for the tracks layer (*Fahrspuren*).
- The field assignment column is called "Feld" instead of "Feldnummer".

## [2.0.0] - 2026-07-02

First release of the new generation with **field catalogue**, **terminal
selection** and **AgGPS support**. Summary of the main changes since 1.2.0
(details in the entries 1.3.0–1.10.1 below):

### Added
- **Field catalogue** (`Felder.csv`) as the central source of field identity.
  Fields without a boundary (tracks only) are supported, several boundaries
  per field are possible.
- **Field management**: create, rename and delete fields (right-click) and
  select fields from a **dropdown** (value relation) instead of typing an ID.
- **Terminal selection** sets the export format (32 terminals; ISOXML v3/v4,
  John Deere Gen4, AgGPS); contour segments only for Fendt One.
- **AgGPS export and import** (folder structure
  `AgGPS/Data/<Customer>/<Farm>/<Field>/` with `boundary.shp`, `swaths.shp`,
  `AreaFeature.shp`, `PointFeature.shp`).

### Changed
- The **export iterates over the field catalogue** instead of the boundaries.
- **Naming model**: the field name comes from the first boundary or is set when
  the field is created; further boundaries keep their own names.

### Fixed
- Stability fixes with an open edit session and for the dropdown (after import,
  in old projects). The field area is **always calculated from the geometry**,
  never read from the file.

## [1.10.1] - 2026-06-25
### Fixed
- AgGPS import: the field area is now **always calculated from the geometry**
  (ellipsoidal via `QgsDistanceArea`) and never read from the file. Previously
  0 was written when the source had no area.

## [1.10.0] - 2026-06-25
### Added
- **AgGPS import**: reads the folder structure
  `AgGPS/Data/<Customer>/<Farm>/<Field>/` with `boundary.shp`, `swaths.shp`,
  `AreaFeature.shp`, `PointFeature.shp` and creates groups, layers and the
  field catalogue. The format is detected automatically when an AgGPS folder
  is selected.

## [1.9.1] - 2026-06-25
### Fixed
- AgGPS export failed for MultiLineString tracks. Straight/curve detection now
  counts vertices robustly (straight = 2, otherwise curve).

### Added
- `boundary.shp` additionally contains the column `Area` (area in m²).

## [1.9.0] - 2026-06-25
### Added
- **AgGPS export** (Trimble/Case/New Holland and others). Creates the folder
  structure `AgGPS/Data/<Customer>/<Farm>/<Field>/` with shapefiles:
  - `boundary.shp` (field boundaries, columns `id`, `Name`)
  - `swaths.shp` (tracks, columns `id`, `Name`; `id` = 0 for straight, 1 for curve)
  - `AreaFeature.shp` (area obstacles)
  - `PointFeature.shp` (point obstacles)
- AgGPS terminals now get a real export instead of a notice.

## [1.8.0] - 2026-06-25
### Changed
- The export selection (ISOXML v3/v4, John Deere Gen4, contour segments) was
  replaced by a **terminal dropdown** (brand + model). The file format is
  derived automatically from the selected terminal:
  - `3.3` → ISOXML v3, `4.2` → ISOXML v4, `Gen4` → John Deere Gen4,
    `AgGPS` → Trimble/Case/New Holland.
- The **contour segments** option is only shown for **Fendt One**.

### Added
- Notice on export when an **AgGPS** terminal is selected (format not yet
  implemented).

## [1.7.0] - 2026-06-18
### Changed
- **Naming model field vs. boundary**: the field name is set once – by the
  **first** boundary or the *Feld hinzufügen* (add field) button. Further
  boundaries of the same field keep their **own** names and no longer change
  the field name.
- **Export**: the `PFD` carries the field name (catalogue); each boundary is
  written as its own `PLN` with its individual name.
- *Feld umbenennen* (rename field) only changes the field name (catalogue), not
  the names of the individual boundaries.

### Fixed
- No more OGR error (-14) and no duplicate field when an edit session is still
  open while the Path Planner is opened.
- The field dropdown now appears **right after the import** and when opening
  **old projects** (the `Felder` layer is reloaded or created so that the value
  relation source is filled).

## [1.6.0] - 2026-06-18
### Added
- **Delete field** via right-click in the selection tree, with confirmation.
  All objects of the field (boundaries, tracks, point and area obstacles) and
  the catalogue entry are removed.
- Protection: deleting is blocked while an affected layer is in edit mode.

## [1.5.0] - 2026-06-18
### Added
- **Field selection by dropdown**: the `ID` field of boundaries, tracks and
  obstacles is now a QGIS value relation dropdown that shows the **field names**
  from the catalogue and stores the `id`. No more looking up numbers.

## [1.4.0] - 2026-06-18
### Added
- **Feld hinzufügen** (add field) button: creates a named field without a
  boundary – ideal for fields that only have tracks.
- **Catalogue synchronisation when the Path Planner opens**: `Felder.csv` is
  synchronised with the layers (incl. automatic registration of IDs from tracks
  and obstacles and automatic ID assignment for newly drawn boundaries).
- **Rename field** via right-click in the selection tree.

## [1.3.0] - 2026-06-18
### Added
- **Field catalogue `Felder.csv`** (`id;Name`) per farm as the central
  "source of truth" for field identity.
- Import (ISOXML **and** John Deere Gen4) writes **every** field to the
  catalogue – also fields without a boundary.

### Changed
- **Export iterates over the catalogue** instead of the boundaries: fields
  without a boundary are exported correctly (PFD without PLN), and a field may
  have several boundaries.
- The selection tree lists all catalogue fields (incl. those without a
  boundary).
- The unique constraint on the `ID` field was changed from **hard** (blocking)
  to **soft** (warning only), so several boundaries may share the same `ID`.

## [1.2.0] - 2025
### Initial version
- Import/export of ISOXML (v3/v4) and John Deere Gen4.
- Structured selection tree (customer → farm → field), optional contour
  segments.
- Field assignment via the *Feldgrenzen* layer.

[2.3.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v2.3.0
[2.2.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v2.2.0
[2.1.1]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v2.1.1
[2.1.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v2.1.0
[2.0.1]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v2.0.1
[2.0.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v2.0.0
[1.7.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.7.0
[1.6.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.6.0
[1.5.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.5.0
[1.4.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.4.0
[1.3.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.3.0
[1.2.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.2.0
