# Versionierung & GitHub – Vorgehen

Ziel: eine saubere Historie ausgehend von **1.2.0** mit **5 Meilensteinen**
(1.3.0 → 1.7.0), jeweils als eigener Commit + Tag.

## Release 2.0.0

Für die Veröffentlichung als **2.0.0** (Feld-Katalog, Terminal-Auswahl, AgGPS):

```bash
git add -A
git commit -m "release: v2.0.0 - field catalogue, terminal selection, AgGPS import/export"
git tag v2.0.0
git push origin main --tags
```

Der Tag `v2.0.0` erzeugt auf GitHub automatisch ein Release; der Changelog steht
in `CHANGELOG.md` und im `changelog=`-Feld der `metadata.txt`.

## Die 5 Meilensteine (Mapping)

| Version | Inhalt | Commit-Nachricht |
|--------|--------|------------------|
| 1.3.0 | Feld-Katalog `Felder.csv`, Export über Katalog, mehrere Grenzen pro Feld, weiche ID-Constraint | `feat: Feld-Katalog (Felder.csv) und katalogbasierter Export` |
| 1.4.0 | „Feld hinzufügen", Katalog-Abgleich beim Öffnen, „Feld umbenennen" | `feat: Felder anlegen, abgleichen und umbenennen` |
| 1.5.0 | ID-Feld als Dropdown (Value Relation) mit Feldnamen | `feat: Feld-Auswahl per Dropdown (Value Relation)` |
| 1.6.0 | „Feld löschen" mit Sicherheitsabfrage | `feat: Felder per Rechtsklick löschen` |
| 1.7.0 | Namensmodell Feld/Grenze, Bugfixes (Edit-Modus, Dropdown nach Import/Altprojekt) | `feat: Namensmodell + Stabilisierung (Edit-Modus, Dropdown)` |

---

## Empfohlenes Vorgehen (echte 5-Schritt-Historie)

Du hast zu jedem Schritt bereits ein lauffähiges ZIP erhalten (Versionen 1.3.0,
1.4.0, 1.5.0, 1.6.0, 1.7.0). Damit lässt sich die Historie sauber nachbauen:

```bash
# 1) Repo vorbereiten (einmalig)
cd lk_technik_path_planner
git init
git branch -M main
git remote add origin https://github.com/lktechnikmold/lk_technik_path_planner.git

# 2) Ausgangsversion 1.2.0 als ersten Commit
#    (Inhalt der hochgeladenen 1.2.0 ins Repo legen)
git add -A
git commit -m "chore: Ausgangsversion 1.2.0"
git tag v1.2.0

# 3) Für JEDEN Meilenstein: Dateien der jeweiligen Version ins Repo kopieren,
#    dann committen + taggen. Beispiel für 1.3.0:
#    (alte Dateien durch die der Version 1.3.0 ersetzen)
git add -A
git commit -m "feat: Feld-Katalog (Felder.csv) und katalogbasierter Export"
git tag v1.3.0

# ... analog 1.4.0, 1.5.0, 1.6.0, 1.7.0 mit den Commit-Nachrichten aus der Tabelle.

# 4) Alles hochladen
git push -u origin main --tags
```

Tipp: Beim Ersetzen der Dateien je Version am einfachsten den **gesamten Ordnerinhalt**
durch das jeweilige ZIP überschreiben (Dateien, die es in einer Version noch nicht
gab, einfach mitnehmen – `git add -A` erkennt Hinzugefügtes/Geändertes/Gelöschtes).

> Falls dir ein Zwischen-ZIP fehlt: einfach melden, ich erzeuge den exakten Stand
> jeder Version neu.

---

## Pragmatische Alternative (1 Commit, dokumentierte Historie)

Wenn dir die Historie als reine Dokumentation reicht, committe den finalen Stand
1.7.0 in einem Rutsch – die `CHANGELOG.md` erzählt die 5 Schritte:

```bash
cd lk_technik_path_planner
git init
git branch -M main
git add -A
git commit -m "feat: v1.7.0 – Feld-Katalog, Dropdown, Anlegen/Umbenennen/Löschen"
git tag v1.7.0
git remote add origin https://github.com/lktechnikmold/lk_technik_path_planner.git
git push -u origin main --tags
```

---

## Konventionen

- **Versionsnummern**: `MAJOR.MINOR.PATCH` (Semantic Versioning). Neue Funktionen
  erhöhen MINOR, reine Bugfixes PATCH.
- **Tags**: `vX.Y.Z` (z. B. `v1.7.0`) – GitHub erzeugt daraus automatisch Releases.
- **`metadata.txt`**: `version=` und das `changelog=`-Feld bei jeder Veröffentlichung
  mitziehen (QGIS zeigt den Changelog im Plugin-Manager).
- **`CHANGELOG.md`**: pro Release einen Abschnitt ergänzen.
