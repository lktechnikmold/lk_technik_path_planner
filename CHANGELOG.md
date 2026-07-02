# Changelog

Alle nennenswerten Änderungen an diesem Plugin werden in dieser Datei dokumentiert.

Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.0.0/),
und das Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

## [2.0.0] - 2026-07-02

Erste Veröffentlichung der neuen Generation mit **Feld-Katalog**, **Terminal-Auswahl**
und **AgGPS-Unterstützung**. Zusammenfassung der wichtigsten Neuerungen seit 1.2.0
(Details siehe die Einträge 1.3.0–1.10.1 weiter unten):

### Hinzugefügt
- **Feld-Katalog** (`Felder.csv`) als zentrale Quelle der Feldidentität. Felder ohne
  Feldgrenze (nur Fahrspuren) werden unterstützt, mehrere Feldgrenzen pro Feld sind möglich.
- **Feldverwaltung**: Felder anlegen, umbenennen und löschen (Rechtsklick) sowie
  Feld-Auswahl per **Dropdown** (Value Relation) statt ID-Eingabe.
- **Terminal-Auswahl** bestimmt das Exportformat (32 Terminals; ISOXML v3/v4,
  John Deere Gen4, AgGPS); Kontursegmente nur bei Fendt One.
- **AgGPS-Export und -Import** (Ordnerstruktur `AgGPS/Data/<Kunde>/<Betrieb>/<Feld>/`
  mit `boundary.shp`, `swaths.shp`, `AreaFeature.shp`, `PointFeature.shp`).

### Geändert
- Der **Export läuft über den Feld-Katalog** statt über die Feldgrenzen.
- **Namensmodell**: Der Feldname stammt von der ersten Feldgrenze bzw. wird beim
  Anlegen gesetzt; weitere Grenzen behalten ihre eigenen Namen.

### Behoben
- Stabilitätsfixes bei offenem Bearbeitungsmodus und beim Anzeigen des Dropdowns
  (nach Import, in Altprojekten). Die Feldfläche wird **immer aus der Geometrie
  berechnet**, nie aus der Datei gelesen.

## [1.10.1] - 2026-06-25
### Behoben
- AgGPS-Import: Die Feldfläche wird jetzt **immer aus der Geometrie berechnet**
  (ellipsoidisch via `QgsDistanceArea`) und nie aus der Datei gelesen. Fehlte die
  Fläche in der Quelle, wurde zuvor 0 geschrieben.

## [1.10.0] - 2026-06-25
### Hinzugefügt
- **AgGPS-Import**: liest die Ordnerstruktur `AgGPS/Data/<Kunde>/<Betrieb>/<Feld>/`
  mit `boundary.shp`, `swaths.shp`, `AreaFeature.shp`, `PointFeature.shp` und legt
  Gruppen, Layer und den Felder-Katalog an. Das Format wird beim Wählen eines
  AgGPS-Ordners automatisch erkannt.

## [1.9.1] - 2026-06-25
### Behoben
- AgGPS-Export brach bei MultiLineString-Fahrspuren ab. Die Geraden/Kurven-
  Erkennung zählt jetzt robust die Stützpunkte (Gerade = 2, sonst Kurve).

### Hinzugefügt
- `boundary.shp` enthält zusätzlich die Spalte `Area` (Fläche in m²).

## [1.9.0] - 2026-06-25
### Hinzugefügt
- **AgGPS-Export** (Trimble/Case/New Holland u.a.). Erzeugt die Ordnerstruktur
  `AgGPS/Data/<Kunde>/<Betrieb>/<Feld>/` mit Shapefiles:
  - `boundary.shp` (Feldgrenzen, Spalten `id`, `Name`)
  - `swaths.shp` (Fahrspuren, Spalten `id`, `Name`; `id` = 0 für Gerade, 1 für Kurve)
  - `AreaFeature.shp` (Flächenhindernisse)
  - `PointFeature.shp` (Punkthindernisse)
- Damit liefern AgGPS-Terminals nun einen echten Export statt eines Hinweises.

## [1.8.0] - 2026-06-25
### Geändert
- Die Exportauswahl (ISOXML v3/v4, John Deere Gen4, Kontursegmente) wurde durch
  ein **Terminal-Dropdown** ersetzt (Marke + Modell). Das Dateiformat wird aus dem
  gewählten Terminal automatisch bestimmt:
  - `3.3` → ISOXML v3, `4.2` → ISOXML v4, `Gen4` → John Deere Gen4,
    `AgGPS` → Trimble/Case/New Holland.
- Die Option **Kontursegmente** erscheint nur noch bei **Fendt One**.

### Hinzugefügt
- Hinweis beim Export, wenn ein **AgGPS**-Terminal gewählt ist (Format noch nicht
  implementiert).

## [1.7.0] - 2026-06-18
### Geändert
- **Namensmodell Feld vs. Feldgrenze**: Der Feldname wird einmalig festgelegt –
  durch die **erste** Feldgrenze oder den Button *Feld hinzufügen*. Weitere
  Feldgrenzen desselben Feldes behalten ihren **eigenen** Namen und verändern den
  Feldnamen nicht mehr.
- **Export**: Das `PFD` trägt den Feldnamen (Katalog); jede Feldgrenze wird als
  eigene `PLN` mit ihrem individuellen Namen geschrieben.
- *Feld umbenennen* ändert nur noch den Feldnamen (Katalog), nicht die Namen der
  einzelnen Grenzen.

### Behoben
- Kein OGR-Fehler (-14) und keine doppelte Feldanlage mehr, wenn der
  Bearbeitungsmodus beim Öffnen des Path Planners noch offen ist.
- Das Feld-Dropdown erscheint jetzt **sofort nach dem Import** und auch beim
  Öffnen **alter Projekte** (der `Felder`-Layer wird frisch geladen bzw.
  sichergestellt, sodass die Value-Relation-Quelle gefüllt ist).

## [1.6.0] - 2026-06-18
### Hinzugefügt
- **Feld löschen** per Rechtsklick im Auswahlbaum, mit Sicherheitsabfrage. Es
  werden alle Objekte des Feldes (Feldgrenze(n), Fahrspuren, Punkt- und
  Flächenhindernisse) sowie der Katalogeintrag entfernt.
- Schutz: Löschen wird blockiert, solange eine betroffene Ebene im
  Bearbeitungsmodus ist.

## [1.5.0] - 2026-06-18
### Hinzugefügt
- **Feld-Auswahl per Dropdown**: Das `ID`-Feld von Feldgrenzen, Fahrspuren und
  Hindernissen ist nun ein QGIS-Value-Relation-Dropdown, das die **Feldnamen**
  aus dem Katalog anzeigt und die `id` speichert. Kein Nachschlagen von Nummern
  mehr nötig.

## [1.4.0] - 2026-06-18
### Hinzugefügt
- Button **Feld hinzufügen**: legt ein Feld (mit Namen) ohne Feldgrenze an –
  ideal für Felder, die nur Fahrspuren haben.
- **Katalog-Abgleich beim Öffnen** des Path Planners: `Felder.csv` wird mit den
  Ebenen synchronisiert (inkl. Auto-Registrierung von IDs aus Fahrspuren und
  Hindernissen sowie automatischer ID-Vergabe für neu gezeichnete Feldgrenzen).
- **Feld umbenennen** per Rechtsklick im Auswahlbaum.

## [1.3.0] - 2026-06-18
### Hinzugefügt
- **Feld-Katalog `Felder.csv`** (`id;Name`) pro Betrieb als zentrale
  „Source of Truth" für die Feldidentität.
- Import (ISOXML **und** John Deere Gen4) schreibt **jedes** Feld in den Katalog
  – auch Felder ohne Feldgrenze.

### Geändert
- **Export läuft über den Katalog** statt über die Feldgrenzen: Felder ohne
  Grenze werden korrekt exportiert (PFD ohne PLN), und ein Feld darf mehrere
  Feldgrenzen besitzen.
- Der Auswahlbaum listet alle Katalog-Felder (inkl. grenzenloser).
- Die Unique-Constraint auf dem Feld `ID` wurde von **hart** (blockierend) auf
  **weich** (nur Warnung) gesetzt, damit mehrere Feldgrenzen dieselbe `ID`
  haben dürfen.

## [1.2.0] - 2025
### Ausgangsversion
- Import/Export von ISOXML (v3/v4) und John Deere Gen4.
- Strukturierter Auswahlbaum (Kunde → Betrieb → Feld), optionale Konturen-Segmente.
- Feldzuordnung über den Layer *Feldgrenzen*.

[1.7.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.7.0
[1.6.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.6.0
[1.5.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.5.0
[1.4.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.4.0
[1.3.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.3.0
[1.2.0]: https://github.com/lktechnikmold/lk_technik_path_planner/releases/tag/v1.2.0
