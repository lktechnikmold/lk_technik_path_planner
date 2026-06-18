# LK-Technik Path Planner

LK-Technik Path Planner – Combined QGIS Plugin (Import & Export)

This plugin provides import and export functionality for guidance data in agricultural workflows, supporting both ISOXML and John Deere Gen4 formats within QGIS.

Developed for QGIS 3.x

---

## Disclaimer

This plugin is provided free of charge in the hope that it will be useful.

It is distributed under the terms of the GNU General Public License v3.0 (or later) and is provided WITHOUT ANY WARRANTY, without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

To the extent permitted by applicable law, the Landwirtschaftskammer Niederösterreich shall not be liable for any damages arising from the use of this plugin.

For full warranty and liability terms, see Sections 15 and 16 of the GNU General Public License.


## Overview

The LK-Technik Path Planner combines:

Import (ISOXML / John Deere Gen4 → QGIS)
Export (QGIS → ISOXML / John Deere Gen4)

It enables seamless exchange of guidance lines and field data between farm machinery and GIS.

The plugin supports structured ISOXML data (e.g. TASKDATA.XML) and integrates directly into the QGIS interface.

---

## Features

- Single dialog with **Import** and **Export** mode
- **Export**:
  - Reads project tree (client → farm → layers)
  - Selectable tree with checkboxes for customers, companies, and fields (from the *Feldgrenzen* layer)
  - Supports **ISOXML v3**, **ISOXML v4** and **John Deere Gen4**
  - Optional contour segmentation (ISOXML v4 only)
  - Optional curve densification and extension at line ends

- **Import**:
  - Reads `TASKDATA.XML`, `MasterData.XML` or Folder with `TASKDATA.XML` or `MasterData.XML` in it.
  - Creates structured layer groups (client → farm)
  - Generates layers:
    - Felder (field catalogue, `Felder.csv`)
    - Feldgrenzen
    - Fahrspuren (with `Segment`)
    - Punkthindernis
    - Flaechenhindernis
  - Optional export per FRM as GeoPackage (GPKG)

### Field catalogue (`Felder.csv`)

Since version 1.3.0 every farm folder additionally contains a `Felder.csv`
(`id;Name`). It is the authoritative list of fields and is what the export
iterates over. Consequences:

- A field (PFD) is created in `Felder.csv` for **every** imported partfield –
  even if it has no boundary. Guidance lines without a *Feldgrenze* are therefore
  no longer lost on export.
- Several *Feldgrenzen* may share the same field `ID`.
- When a new *Feldgrenze* is committed in QGIS, a matching entry is created in
  `Felder.csv` automatically and a missing `ID` is assigned.
- Fields with **only guidance lines** (no boundary) can be created in two ways:
  1. Button **Feld hinzufügen** in the export view – creates a named catalogue
     entry and assigns the next free `ID`, which you then enter on the tracks.
  2. Draw the tracks, set their `ID`, and open the Path Planner – the catalogue
     is synchronised on open and picks up the track `ID`s automatically.

The catalogue is rebuilt from all layers (Feldgrenzen, Fahrspuren, obstacles)
every time the Path Planner dialog is opened, so `Felder.csv` always stays in
sync even if a layer was edited outside the plugin.

### Field name vs. boundary name

A field can have several boundaries with **different** names. The naming model is:

- The **field** (catalogue) name is set **once** – from the **first** boundary
  drawn for it, or from the name given in *Feld hinzufügen*. Additional
  boundaries added later to the same field keep their **own** names; those do not
  change the field name.
- Rename the field via right-click → *Feld umbenennen…* (this changes only the
  catalogue entry, not the individual boundary names).
- On export the `PFD` name is the field (catalogue) name, while each boundary is
  written as its own `PLN` carrying that boundary's individual name. Fields with
  several boundaries therefore produce several `PLN` elements.

Since version 1.5.0 the `ID` field of Feldgrenzen, Fahrspuren and the obstacle
layers is shown as a **dropdown** (QGIS value relation) listing the field names
from the catalogue. You pick a field by name; the numeric `id` is stored behind
the scenes, so there is no need to look up which field has which id. The dropdown
is (re)configured each time the Path Planner is opened, pointing at the `Felder`
layer of the same farm.

Note: CSV layers are read-only inside QGIS (delimitedtext provider). The plugin
manages `Felder.csv` programmatically; edit field names directly in the file or
via the *Feldgrenze* attributes. The file is always written with `;` as the
delimiter (UTF-8 with BOM).


## Usage

- Open **LK-Technik Path Planner** from the toolbar or menu
- Select **Import** or **Export**
- Configure options
- Click **Run**

---

## Governance

This plugin is developed and published by:

Landwirtschaftskammer Niederösterreich  
Website: https://noe.lko.at

Primary Developer:  
Florian Köck

### Maintenance

The Landwirtschaftskammer Niederösterreich acts as the institutional maintainer of this project.

The primary developer is responsible for:

- Feature development
- Bug fixing
- Version releases
- Compatibility with supported QGIS versions

### Contributions

This project is primarily developed and maintained internally.

External contributions are not actively solicited. If necessary, contributions may be considered on a case-by-case basis and must align with project standards and licensing requirements.

### Issue Tracking

Bug reports and feature requests should be submitted via the official issue tracker of the project repository.

---

## License

Copyright (C) 2024–2026  
Landwirtschaftskammer Niederösterreich

Developed by Florian Köck.

This project is licensed under the GNU General Public License v3.0 (or later).

See the LICENSE file for full license text.
