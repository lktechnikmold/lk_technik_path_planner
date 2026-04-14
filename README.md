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
    - Feldgrenzen
    - Fahrspuren (with `Segment`)
    - Punkthindernis
    - Flaechenhindernis
  - Optional export per FRM as GeoPackage (GPKG)


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
