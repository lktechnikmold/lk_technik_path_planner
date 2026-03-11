# ISOXML Toolbox (QGIS Plugin)

ISOXML Toolbox – Combined QGIS Plugin (Import & Export)

This plugin provides import and export functionality for ISOXML data within QGIS.

Developed for QGIS 3.x

---

## Disclaimer

This plugin is provided free of charge in the hope that it will be useful.

It is distributed under the terms of the GNU General Public License v3.0 (or later) and is provided WITHOUT ANY WARRANTY, without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

To the extent permitted by applicable law, the Landwirtschaftskammer Niederösterreich shall not be liable for any damages arising from the use of this plugin.

For full warranty and liability terms, see Sections 15 and 16 of the GNU General Public License.


## Overview

The ISOXML Toolbox combines:

- Import (ISOXML → QGIS)
- Export (QGIS → ISOXML)

It supports structured ISOXML data (e.g. TASKDATA.XML) and integrates into the QGIS interface.

---

## Features

- Single dialog with **Import** and **Export** mode
- **Export**:
  - Reads project tree (client → farm → layers)
  - Selectable tree with checkboxes for customers, companies, and fields (from the *Feldgrenzen* layer)
  - Supports **ISOXML v3** and **ISOXML v4**
  - Optional contour segmentation (v4 only)

- **Import**:
  - Reads `TASKDATA.XML`
  - Creates structured layer groups (client → farm)
  - Generates layers:
    - Feldgrenzen
    - Fahrspuren (with `Segment`)
    - Punkthindernis
    - Flaechenhindernis
  - Optional export per FRM as GeoPackage (GPKG)


## Usage

- Open **ISOXML Toolbox (Import/Export)** from the toolbar or menu
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

External contributions are welcome.

Contributors must:

- Submit changes via pull requests in the official repository
- Agree that contributions are licensed under the GNU General Public License v3.0 (or later)
- Ensure no proprietary or incompatible third-party code is introduced

### Issue Tracking

Bug reports and feature requests should be submitted via the official issue tracker of the project repository.

---

## License

Copyright (C) 2024–2025  
Landwirtschaftskammer Niederösterreich

Developed by Florian Köck.

This project is licensed under the GNU General Public License v3.0 (or later).

See the LICENSE file for full license text.
