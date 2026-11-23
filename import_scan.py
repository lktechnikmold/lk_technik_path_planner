# -*- coding: utf-8 -*-
"""
Sucht in einem Ordner (rekursiv) alle Lenksystemdaten, die das Plugin
importieren kann. Jeder Fund ist ein Eintrag für die Auswahl vor dem Import:

    ISOXML  TASKDATA.XML
    Gen4    Ordner mit MasterData.xml
    AgGPS   Data-Ordner mit <Kunde>/<Betrieb>/<Feld>/*.shp
    CN1     <ID>.cn1-Ordner bzw. ZIP mit CN1-Daten
    KML     AGCO-KML (.kml oder Fendt-ZIP), je Feld eine Datei

Ein erkannter Gen4-, AgGPS- oder CN1-Ordner wird nicht weiter durchsucht.
Eine TASKDATA neben einer .kml gehört zum Fendt-Export (Hybrid) und liefert
dort nur Kunde/Betrieb - sie wird nicht als eigene Datei aufgeführt. Dieselbe
KML doppelt (ZIP und daneben entpackt) erscheint nur einmal.
"""

import os
import zipfile

try:
    from . import agco_kml_format as kml
    from .aggps_import import _looks_like_aggps_data
except Exception:
    import agco_kml_format as kml
    from aggps_import import _looks_like_aggps_data

KINDS = ("ISOXML", "Gen4", "AgGPS", "CN1", "KML")


def _entry(kind, path, label, base):
    rel = os.path.relpath(path, base) if base else path
    return {"kind": kind, "path": path, "label": label, "rel": "." if rel == "." else rel}


def _kml_label(path):
    names = []
    for kml_data, ini_data, _task, stem in kml.read_sources(path):
        try:
            name = kml.parse_kml(kml_data)["name"]
        except Exception:
            name = ""
        names.append(name or (kml.ini_name(ini_data) if ini_data else "") or kml.name_from_stem(stem))
    return ", ".join(n for n in names if n)


def _kml_members(path):
    """Inhalte aller KML einer Datei/ZIP (zum Erkennen doppelter Kopien)."""
    return tuple(item[0] for item in kml.read_sources(path))


def _zip_has_cn1(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return any(n.lower().endswith("index.vy1") for n in zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def find_datasets(folder):
    """-> Liste {"kind", "path", "label", "rel"} in Ordnerreihenfolge."""
    found, seen_kml = [], set()
    for dirpath, dirs, files in os.walk(folder):
        dirs.sort()
        lower = {f.lower(): f for f in files}
        name = os.path.basename(dirpath) or dirpath

        if "index.vy1" in lower and os.path.isdir(os.path.join(dirpath, "shared")):
            found.append(_entry("CN1", dirpath, name, folder))
            dirs[:] = []
            continue
        if "masterdata.xml" in lower:
            found.append(_entry("Gen4", dirpath, name, folder))
            dirs[:] = []
            continue
        if _looks_like_aggps_data(dirpath):
            found.append(_entry("AgGPS", dirpath, name, folder))
            dirs[:] = []
            continue

        if "taskdata.xml" in lower:
            parent = os.path.dirname(dirpath)
            hybrid = os.path.basename(dirpath).upper() == "TASKDATA" and any(
                f.lower().endswith(".kml") for f in os.listdir(parent))
            if not hybrid:
                path = os.path.join(dirpath, lower["taskdata.xml"])
                found.append(_entry("ISOXML", path, os.path.relpath(path, folder), folder))

        for fn in sorted(files):
            full = os.path.join(dirpath, fn)
            low = fn.lower()
            if low.endswith(".kml") or (low.endswith(".zip") and kml.zip_has_kml(full)):
                members = _kml_members(full)
                if members and all(m in seen_kml for m in members):
                    continue
                seen_kml.update(members)
                found.append(_entry("KML", full, _kml_label(full) or fn, folder))
            elif low.endswith(".zip") and _zip_has_cn1(full):
                found.append(_entry("CN1", full, fn, folder))
    return found
