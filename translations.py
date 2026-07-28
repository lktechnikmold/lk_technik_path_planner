# -*- coding: utf-8 -*-
"""
Mehrsprachigkeit (Deutsch/Englisch) fuer LK-Technik Path Planner.

Uebersetzt NUR die Anzeige (UI-Texte, Layer-Anzeigenamen im Layers-Panel,
Feld-Aliase in der Attributtabelle). Die tatsaechlichen Layer-/Feldnamen
(layer.name(), Feldnamen, Felder.csv, .qml-Styles) bleiben immer Deutsch,
damit interne Verweise (Style-Zuordnung, Felder.csv-Automatik, John-Deere-
und AgGPS-Import/Export) unveraendert funktionieren.
"""

from qgis.PyQt.QtCore import QSettings

SETTINGS_KEY = "LkTechnikPathPlanner/language"
DEFAULT_LANGUAGE = "de"

LANGUAGES = {
    "de": "Deutsch",
    "en": "English",
}

_current_language = {"code": DEFAULT_LANGUAGE}


def load_saved_language() -> str:
    """Liest die zuletzt gespeicherte Sprache aus den QGIS-Settings."""
    try:
        code = QSettings().value(SETTINGS_KEY, DEFAULT_LANGUAGE)
    except Exception:
        code = DEFAULT_LANGUAGE
    if code not in LANGUAGES:
        code = DEFAULT_LANGUAGE
    _current_language["code"] = code
    return code


def get_language() -> str:
    return _current_language["code"]


def set_language(code: str) -> None:
    if code not in LANGUAGES:
        code = DEFAULT_LANGUAGE
    _current_language["code"] = code
    try:
        QSettings().setValue(SETTINGS_KEY, code)
    except Exception:
        pass


def tr(text: str) -> str:
    """Uebersetzt einen (deutschen) UI-Text. Ohne Eintrag/auf Deutsch: Original."""
    if _current_language["code"] == "de":
        return text
    return TRANSLATIONS.get(_current_language["code"], {}).get(text, text)


# Deutsch->Englisch-Namen der fixen Layer.
#
# WICHTIG: QgsLayerTreeLayer.setName() aendert bei einem gueltigen/geladenen
# Layer tatsaechlich den echten QgsMapLayer-Namen (layer.name()) - es gibt in
# QGIS KEINEN rein kosmetischen Anzeigenamen unabhaengig davon. Eine sichtbare
# Umbenennung im Layers-Panel bedeutet also zwangslaeufig eine echte Umbenennung.
#
# Damit das nicht Style-Zuordnung, Felder.csv-Automatik und JD/AgGPS-Export
# bricht, MUSS jede Stelle im Code, die Layer anhand ihres Namens sucht oder
# vergleicht, ueber canonical_layer_name() gehen statt den String direkt zu
# vergleichen. layer.source() (GPKG-Pfad/Tabellenname), Felder.csv und die
# .qml-Stylenamen sind davon nicht betroffen - sie haengen nicht an layer.name().
LAYER_DISPLAY_NAMES = {
    "en": {
        "Felder": "Fields",
        "Feldgrenzen": "Boundaries",
        "Fahrspuren": "Swaths",
        "Punkthindernis": "PointFeature",
        "Flaechenhindernis": "AreaFeature",
    },
}

# {Kanon-Name (Deutsch) -> {alle bekannten Namensvarianten}}, aus LAYER_DISPLAY_NAMES abgeleitet.
LAYER_NAME_VARIANTS = {
    canon: {canon} | {names.get(canon) for names in LAYER_DISPLAY_NAMES.values() if names.get(canon)}
    for canon in LAYER_DISPLAY_NAMES.get("en", {})
}


def canonical_layer_name(name: str) -> str:
    """Bildet einen (evtl. uebersetzten) Layernamen auf den deutschen Kanon-Namen ab."""
    for canon, variants in LAYER_NAME_VARIANTS.items():
        if name in variants:
            return canon
    return name


def display_layer_name(name: str, lang: str = None) -> str:
    """Anzeigename fuer einen (Kanon- oder Varianten-)Layernamen in der gewuenschten Sprache."""
    canon = canonical_layer_name(name)
    lang = lang if lang is not None else get_language()
    return LAYER_DISPLAY_NAMES.get(lang, {}).get(canon, canon)

# Feld-Aliase in der Attributtabelle (nur Anzeige, echter Feldname bleibt unveraendert).
# "ID"/"id" verweist auf das zugehoerige Feld (Value-Relation-Dropdown) und
# wird daher in beiden Sprachen als "Feld"/"Field" beschriftet statt als "ID".
FIELD_ALIASES = {
    "de": {
        "ID": "Feld",
        "id": "Feld",
    },
    "en": {
        "Flaeche": "Area",
        "befahrbar": "Passable",
        "ID": "Field",
        "id": "Field",
    },
}

TRANSLATIONS = {
    "en": {
        # --- ToolboxDialog: allgemein ---
        "Betrieb hinzufügen": "Add farm",
        "KBS": "CRS",
        "Projekt-KBS": "Project CRS",
        "Abbrechen": "Cancel",
        "Feld hinzufügen": "Add field",
        "z.B. Hausacker": "e.g. Home field",
        "Es wird ein Feld ohne Feldgrenze im Katalog (Felder.csv) angelegt.\n"
        "Die vergebene ID kannst du anschließend den Fahrspuren zuweisen.":
            "A field without a boundary will be added to the catalog (Felder.csv).\n"
            "You can then assign the given ID to the swaths.",
        "Betrieb:": "Farm:",
        "Feldname:": "Field name:",
        "Kunde auswählen:": "Select customer:",
        "Betriebsname:": "Farm name:",
        "Ausführen": "Run",
        "Schließen": "Close",
        "Export-Optionen": "Export options",
        "Zielordner für Export wählen": "Select export target folder",
        "Zielordner:": "Target folder:",
        "Kontursegmente": "Contour segments",
        "Format:": "Format:",
        "Erweiterte Einstellungen": "Advanced settings",
        "Kurven nach Intervall verdichten": "Densify curves by interval",
        "Kurven an den Enden verlängern": "Extend curves at the ends",
        "Kunde / Betrieb / Feld": "Customer / Farm / Field",
        "Wähle, was exportiert werden soll:": "Select what to export:",
        "Kunde hinzufügen": "Add customer",
        "Import-Optionen": "Import options",
        "Datei…": "File…",
        "Ordner…": "Folder…",
        "TASKDATA.XML oder MasterData.xml wählen": "Select TASKDATA.XML or MasterData.xml",
        "XML (*.xml);;Alle Dateien (*)": "XML (*.xml);;All files (*)",
        "Ordner wählen (Gen4 / AgGPS / ISOXML)": "Select folder (Gen4 / AgGPS / ISOXML)",
        "TASKDATA.XML, Gen4- oder AgGPS-Ordner:": "TASKDATA.XML, Gen4 or AgGPS folder:",
        "Ausgabe-Ordner (optional)": "Output folder (optional)",
        "Ausgabe Ordner (GPKG, optional):": "Output folder (GPKG, optional):",
        "Koordinatensystem für GPKG (Import)": "Coordinate system for GPKG (import)",
        "Geometrien als WGS84 speichern (empfohlen).": "Save geometries as WGS84 (recommended).",
        "Geometrien ins aktuelle Projekt-KBS transformieren und so speichern.":
            "Transform and save geometries into the current project CRS.",
        "Hinweis: Ohne Ausgabe-Ordner werden die Layer als Temporärlayer geladen und "
        "können nicht direkt wieder exportiert werden!":
            "Note: Without an output folder, layers are loaded as temporary layers "
            "and cannot be exported again directly!",

        # --- Plugin-Menue ---
        "LK-Technik Path Planner (Import/Export)": "LK-Technik Path Planner (Import/Export)",

        # --- Meldungen: Styles/Farben ---
        "Style-Warnung": "Style warning",
        "Style für Layer '{name}' konnte nicht geladen werden: {msg}":
            "Style for layer '{name}' could not be loaded: {msg}",
        "Style-Fehler": "Style error",
        "Fehler beim Laden des Styles für '{name}': {e}":
            "Error loading the style for '{name}': {e}",
        "Farb-Fehler": "Color error",
        "Farbe für Feldgrenzen konnte nicht gesetzt werden: {e}":
            "Could not set color for boundaries: {e}",

        # --- Meldungen: Kunde/Betrieb/Feld anlegen ---
        "Ablageordner für neue Betriebe wählen": "Select storage folder for new farms",
        "Abgebrochen": "Cancelled",
        "Kein Ablageordner gewählt – Betrieb wurde nicht erstellt.":
            "No storage folder selected – farm was not created.",
        "Kundenname:": "Customer name:",
        "OK": "OK",
        "Kunde '{name}' angelegt.": "Customer '{name}' created.",
        "Hinweis": "Note",
        "Es gibt noch keinen Kunden. Bitte zuerst einen Kunden anlegen.":
            "There is no customer yet. Please create a customer first.",
        "Fehler": "Error",
        "Konnte Betrieb/Layers nicht erstellen: {e}": "Could not create farm/layers: {e}",
        "Betrieb '{frm_name}' mit Layern erstellt ({crs}).":
            "Farm '{frm_name}' created with layers ({crs}).",
        "Es gibt noch keinen Betrieb. Bitte zuerst einen Betrieb anlegen.":
            "There is no farm yet. Please create a farm first.",
        "Nicht möglich": "Not possible",
        "Die Layer dieses Betriebs sind noch temporär (nicht gespeichert).\n"
        "Bitte zuerst dauerhaft als GeoPackage speichern, dann erneut versuchen.":
            "The layers of this farm are still temporary (not saved).\n"
            "Please save them permanently as a GeoPackage first, then try again.",
        "Feld '{name}' angelegt (ID {new_id}). Weise diese ID den Fahrspuren zu.":
            "Field '{name}' created (ID {new_id}). Assign this ID to the swaths.",

        # --- Meldungen: Feld umbenennen/loeschen ---
        "Feld umbenennen…": "Rename field…",
        "Feld löschen…": "Delete field…",
        "Umbenennen fehlgeschlagen: {e}": "Rename failed: {e}",
        "Löschen fehlgeschlagen: {e}": "Delete failed: {e}",
        "Die Layer dieses Betriebs sind noch temporär (nicht gespeichert).":
            "The layers of this farm are still temporary (not saved).",
        "Bearbeitung aktiv": "Editing active",
        "Bitte zuerst den Bearbeitungsmodus schließen für: {names}.":
            "Please close edit mode first for: {names}.",
        "Feld {field_id}": "Field {field_id}",
        "  • {name}: {count} Objekt(e)": "  • {name}: {count} object(s)",
        "  • (keine Geometrien – nur Katalogeintrag)":
            "  • (no geometries – catalog entry only)",
        "Feld löschen": "Delete field",
        "Sind Sie sicher, dass Sie das Feld „{label}“ (ID {field_id}) löschen möchten?":
            "Are you sure you want to delete field \u201e{label}\u201c (ID {field_id})?",
        "Damit werden ALLE Daten dieses Feldes unwiderruflich gelöscht – "
        "Feldgrenze(n), Fahrspuren, Hindernisse und der Katalogeintrag:\n\n":
            "This will permanently delete ALL data for this field – "
            "boundary/boundaries, swaths, obstacles and the catalog entry:\n\n",
        "Ja, löschen": "Yes, delete",
        "Feld „{label}“ (ID {field_id}) wurde gelöscht.":
            "Field \u201e{label}\u201c (ID {field_id}) has been deleted.",
        "Feld umbenennen": "Rename field",
        "Neuer Name für Feld (ID {field_id}):": "New name for field (ID {field_id}):",
        "Feld (ID {field_id}) umbenannt in '{new_name}'.":
            "Field (ID {field_id}) renamed to '{new_name}'.",

        # --- Meldungen: Import ---
        "Keine Datei oder kein Ordner gewählt.": "No file or folder selected.",
        "Im gewählten Ordner wurde weder eine MasterData.xml noch eine "
        "TASKDATA.XML gefunden.":
            "Neither a MasterData.xml nor a TASKDATA.XML was found in the "
            "selected folder.",
        "Keine ISOXML-Datei gewählt.": "No ISOXML file selected.",
        "XML-Parsing fehlgeschlagen: {e}": "XML parsing failed: {e}",
        "ISOXML importiert (CTR → FRM → Layer).":
            "ISOXML imported (Customer → Farm → Layer).",

        # --- Meldungen: Export ---
        "Export nicht möglich": "Export not possible",
        "Folgende exportrelevante Layer sind noch temporär:\n"
        "{preview}\n\n"
        "Bitte diese Layer zuerst dauerhaft speichern.":
            "The following export-relevant layers are still temporary:\n"
            "{preview}\n\n"
            "Please save these layers permanently first.",
        "\n… und {n} weitere": "\n… and {n} more",
        "Bitte ein Terminal auswählen.": "Please select a terminal.",
        "Bitte Zielordner wählen.": "Please select a target folder.",
        "Keine Auswahl getroffen.": "Nothing selected.",
        "Erfolgreich": "Success",
        "AgGPS-Export erstellt: {path}": "AgGPS export created: {path}",
        "Für die Auswahl gab es keine exportierbaren Daten.":
            "There was no exportable data for the selection.",
        "AgGPS-Export fehlgeschlagen: {e}": "AgGPS export failed: {e}",
        "John Deere Gen4 Export erstellt: {out_dir}":
            "John Deere Gen4 export created: {out_dir}",
        "John Deere Gen4 Export fehlgeschlagen: {e}":
            "John Deere Gen4 export failed: {e}",
        "Konnte XML nicht schreiben: {e}": "Could not write XML: {e}",
        "Keine passenden Gruppen/Layer gefunden – leere TASKDATA.XML geschrieben.":
            "No matching groups/layers found – empty TASKDATA.XML written.",
        "TASKDATA.XML geschrieben: {path}": "TASKDATA.XML written: {path}",
    },
}
