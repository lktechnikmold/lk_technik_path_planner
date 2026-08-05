# -*- coding: utf-8 -*-
"""
LK-Technik Path Planner – combined QGIS Plugin (Import & Export)

Dieses Plugin vereint Import und Export von ISOXML-Daten:
- Import (ISOXML/Gen4 → QGIS)
- Export (QGIS → ISOXML/Gen4)

Entwickelt für QGIS 3.x

Copyright (C) 2024–2026  Florian Köck, LK-Technik Mold
E-Mail: florian.koeck@lk-noe.at
Website: https://www.lk-technik.at
Organisation: Landwirtschaftskammer Niederösterreich

Dieses Programm ist freie Software: Sie können es unter den Bedingungen der
GNU General Public License, Version 3 oder (nach Ihrer Wahl) jeder späteren
Version, wie sie von der Free Software Foundation veröffentlicht wurde,
weitergeben und/oder modifizieren.

Dieses Programm wird in der Hoffnung verbreitet, dass es nützlich sein wird,
jedoch OHNE JEDE GEWÄHRLEISTUNG; sogar ohne die implizite Gewährleistung
der MARKTFÄHIGKEIT oder EIGNUNG FÜR EINEN BESTIMMTEN ZWECK. Siehe die
GNU General Public License für weitere Details.

Eine Kopie der GNU General Public License sollte zusammen mit diesem Programm
mitgeliefert worden sein. Falls nicht, siehe <https://www.gnu.org/licenses/>.

Hinweis:
Gemäß GNU GPL müssen bei Weitergabe oder Modifikation die ursprünglichen
Copyright- und Autorhinweise (Florian Köck, LK-Technik Mold) erhalten bleiben.


Author: Florian Köck
Institution: LK-Technik Mold
Version: 2.1.1
Date: 2026-07-28

Hinweis zu nosec-Markierungen B110/B112: breite except Exception: pass/
continue-Bloecke werden hier bewusst verwendet, um robust gegenueber
Unterschieden zwischen QGIS-/PyQt-Versionen zu bleiben (z.B. optionale
API-Methoden, die je nach QGIS-Version fehlen koennen). Es werden dabei
keine sicherheitsrelevanten Pruefungen uebersprungen.
"""


import os, os.path, math, csv
# Nur zum Aufbauen/Serialisieren der selbst erzeugten TASKDATA.XML beim Export
# (kein Parsen fremder/nicht vertrauenswuerdiger Daten hier) - defusedxml
# bietet dafuer keine Entsprechung (nur eine sichere Parse-Fassade), daher
# hier bewusst die Standardbibliothek.
import xml.etree.ElementTree as ET  # nosec
import processing

# Nur zum Parsen (TASKDATA.XML stammt vom Terminal/Fremdsoftware, also nicht
# vertrauenswuerdig) - gehaertete Variante statt xml.etree.ElementTree, um
# XXE-/Entity-Expansion-Angriffe auszuschliessen.
try:
    from .defusedxml import ElementTree as _SafeET
except Exception:
    from defusedxml import ElementTree as _SafeET

from qgis.PyQt.QtCore import Qt, QVariant, QUrl, QUrlQuery, pyqtSignal
from qgis.PyQt.QtGui import QIcon, QPixmap, QColor
try:
    from . import resources
except Exception:
    import resources
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QFileDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLineEdit, QLabel, QGroupBox, QCheckBox, QRadioButton, QStackedWidget,
    QFormLayout, QInputDialog, QMessageBox, QWidget, QToolButton, QDoubleSpinBox, QButtonGroup, QComboBox, QMenu
)
try:
    from .john_deere_gen4_export import export_john_deere_gen4
except Exception:
    from john_deere_gen4_export import export_john_deere_gen4

try:
    from .aggps_export import export_aggps
except Exception:
    from aggps_export import export_aggps

try:
    from .john_deere_gen4_import import import_john_deere_gen4
except Exception:
    from john_deere_gen4_import import import_john_deere_gen4

try:
    from .aggps_import import import_aggps, detect_aggps_data_root
except Exception:
    from aggps_import import import_aggps, detect_aggps_data_root

try:
    from . import translations
except Exception:
    import translations

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsField, QgsFields, QgsFeature, QgsGeometry, QgsPointXY,
    QgsLayerTreeGroup, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsVectorFileWriter,
    QgsFeatureSink, QgsWkbTypes, QgsEditorWidgetSetup
)

def _tr(message: str) -> str:
    return translations.tr(message)


def _display_layer_name(name: str) -> str:
    """Uebersetzter Anzeigename fuer einen (deutschen oder englischen) Layer-Namen."""
    return translations.display_layer_name(name)


def _canon_layer_name(name: str) -> str:
    """Deutscher Kanon-Name fuer einen (evtl. bereits uebersetzten) Layer-Namen."""
    return translations.canonical_layer_name(name)

def _is_nullish(v):
    if v is None:
        return True
    try:
        if isinstance(v, float) and math.isnan(v):
            return True
    except Exception:  # nosec B110
        pass
    s = str(v).strip()
    if s == "":
        return True
    if s.lower() in {"null", "none", "nan", "<null>"}:
        return True
    return False

def _safe(name: str) -> str:
    return (name or "_untitled_").replace(os.sep, "_").replace("/", "_").strip()

def _norm_name(s: str) -> str:
    return " ".join((s or "").split())

def _field_map(layer: QgsVectorLayer) -> dict:
    return {f.name().lower(): f.name() for f in layer.fields()}

def _pick_field(fmap: dict, *candidates: str):
    for c in candidates:
        n = fmap.get(c.lower())
        if n:
            return n
    return None

def _feat_val(feat: QgsFeature, fmap: dict, *candidates: str, default=None):
    fn = _pick_field(fmap, *candidates)
    if fn is None:
        return default
    try:
        return feat[fn]
    except Exception:
        return default

FELDER_LAYER_NAME = "Felder"
FELDER_CSV_NAME = "Felder.csv"
FELDER_CSV_DELIM = ";" 

# ============================================================
# Terminals -> Export-Dateiformat
# Format-Codes: "3.3" = ISOXML v3, "4.2" = ISOXML v4,
#               "Gen4" = John Deere Gen4, "AgGPS" = Trimble/Case/NH
# ============================================================
TERMINALS = [
    ("ISOXML", "v3", "3.3"),
    ("ISOXML", "v4", "4.2"),
    ("Case/Steyr", "AFS Pro 700", "3.3"),
    ("Case/Steyr", "AFS Pro 1200", "4.2"),
    ("Case/Steyr", "FM 750", "AgGPS"),
    ("Case/Steyr", "FM 1000", "AgGPS"),
    ("Case/Steyr", "S-Fleet Pro", "4.2"),
    ("CHC", "NAV", "4.2"),
    ("Claas", "Cemis 1200", "4.2"),
    ("Claas", "S10", "3.3"),
    ("Deutz", "iMonitor 3", "4.2"),
    ("Fendt", "One", "4.2"),
    ("Fendt", "Vario Terminal INT 01", "4.2"),
    ("FJ", "Dynamics", "4.2"),
    ("John Deere", "GS4", "Gen4"),
    ("John Deere", "GS5", "Gen4"),
    ("John Deere", "GS5+", "Gen4"),
    ("Massey Ferguson", "Datatronic 5", "4.2"),
    ("Massey Ferguson", "Fieldstar 5", "4.2"),
    ("New Holland", "FM 750", "AgGPS"),
    ("New Holland", "FM 1000", "AgGPS"),
    ("New Holland", "Intelli View 4", "3.3"),
    ("Raven", "CR7", "4.2"),
    ("Raven", "CR12", "4.2"),
    ("Raven", "Viper 4", "4.2"),
    ("Raven", "Viper 4+", "4.2"),
    ("Sveaverken", "Autosteer", "4.2"),
    ("Topcon", "X25", "4.2"),
    ("Topcon", "X35", "4.2"),
    ("Topcon", "XD", "4.2"),
    ("Topcon", "XD+", "4.2"),
    ("Trimble", "CFX 750", "AgGPS"),
    ("Trimble", "FM 750", "AgGPS"),
    ("Trimble", "FM 1000", "AgGPS"),
    ("Trimble", "FMX 750", "AgGPS"),
    ("Trimble", "GFX 750", "AgGPS"),
    ("Trimble", "TMX 2050", "AgGPS"),
    ("Valtra", "Smart Touch", "4.2"),
]

# Standard-Terminal (Vorauswahl)
DEFAULT_TERMINAL = ("Fendt", "One", "4.2")


def _format_label(fmt: str) -> str:
    return {
        "3.3": "ISOXML v3",
        "4.2": "ISOXML v4",
        "Gen4": "John Deere Gen4",
        "AgGPS": "AgGPS",
    }.get(fmt, fmt)


def _is_fendt_one(brand: str, model: str) -> bool:
    return brand == "Fendt" and model == "One"


def _felder_csv_path_in_dir(base_dir: str) -> str:
    """Pfad zur Felder.csv in einem Betriebsordner."""
    if not base_dir:
        return ""
    return os.path.join(base_dir, FELDER_CSV_NAME)


def _felder_csv_path_for_layer(layer: QgsVectorLayer) -> str:
    """
    Ermittelt die Felder.csv neben einem datei-basierten Layer
    (z.B. Feldgrenzen.gpkg). Memory-Layer liefern "".
    """
    if not isinstance(layer, QgsVectorLayer):
        return ""
    try:
        if layer.providerType() != "ogr":
            return ""
        src = layer.source() or ""
        # "….gpkg|layername=Feldgrenzen" -> Pfad vor dem |
        gpkg_path = src.split("|", 1)[0].strip()
        if not gpkg_path:
            return ""
        base_dir = os.path.dirname(gpkg_path)
        if not base_dir:
            return ""
        return _felder_csv_path_in_dir(base_dir)
    except Exception:
        return ""


def _read_felder_csv(csv_path: str) -> dict:
    """
    Liest Felder.csv und liefert {int_id: name}.
    Robust gegenüber fehlender Datei / abweichenden Spaltennamen / Trennzeichen.
    """
    rows = {}
    if not csv_path or not os.path.exists(csv_path):
        return rows
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            delim = FELDER_CSV_DELIM
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
                delim = dialect.delimiter
            except Exception:  # nosec B110
                pass
            reader = csv.reader(fh, delimiter=delim)
            header = next(reader, None)
            if header is None:
                return rows
            hmap = {str(h).strip().lower(): i for i, h in enumerate(header)}
            id_idx = hmap.get("id", 0)
            name_idx = hmap.get("name", 1 if len(header) > 1 else 0)
            for rec in reader:
                if not rec:
                    continue
                try:
                    raw_id = rec[id_idx] if id_idx < len(rec) else ""
                    fid = int(str(raw_id).strip())
                except Exception:  # nosec B112
                    continue
                name = rec[name_idx].strip() if name_idx < len(rec) else ""
                rows[fid] = name
    except Exception:  # nosec B110
        pass
    return rows


def _write_felder_csv(csv_path: str, rows: dict) -> bool:
    """
    Schreibt {id: name} nach Felder.csv (Header: id;Name), sortiert nach id.
    """
    if not csv_path:
        return False
    try:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=FELDER_CSV_DELIM)
            writer.writerow(["id", "Name"])
            for fid in sorted(rows.keys()):
                writer.writerow([fid, rows.get(fid, "")])
        return True
    except Exception:
        return False


def _felder_layer_uri(csv_path: str) -> str:
    """Baut die delimitedtext-URI für Felder.csv (ohne Geometrie)."""
    url = QUrl.fromLocalFile(csv_path)
    q = QUrlQuery()
    q.addQueryItem("type", "csv")
    q.addQueryItem("delimiter", FELDER_CSV_DELIM)
    q.addQueryItem("detectTypes", "yes")
    q.addQueryItem("geomType", "none")
    q.addQueryItem("watchFile", "no")
    url.setQuery(q)
    return url.toString()


def _load_felder_layer(csv_path: str) -> QgsVectorLayer:
    """Lädt Felder.csv als (read-only) delimitedtext-Layer namens 'Felder'."""
    if not csv_path:
        return None
    lyr = QgsVectorLayer(_felder_layer_uri(csv_path), FELDER_LAYER_NAME, "delimitedtext")
    return lyr if lyr.isValid() else None


def _felder_rows_from_layer(felder_layer: QgsVectorLayer) -> dict:
    """Liest {id: name} direkt aus einem geladenen Felder-Layer."""
    rows = {}
    if not isinstance(felder_layer, QgsVectorLayer) or not felder_layer.isValid():
        return rows
    fmap = _field_map(felder_layer)
    id_f = _pick_field(fmap, "id", "ID")
    name_f = _pick_field(fmap, "Name", "name")
    for feat in felder_layer.getFeatures():
        try:
            fid = int(feat[id_f]) if id_f else None
        except Exception:
            fid = None
        if fid is None:
            continue
        rows[fid] = str(feat[name_f]).strip() if name_f else ""
    return rows


def _find_child_layer(group: QgsLayerTreeGroup, name: str) -> QgsVectorLayer:
    """
    Findet einen direkten Kind-Layer einer Gruppe anhand des Namens.
    Vergleicht ueber den Kanon-Namen, damit sowohl der deutsche als auch der
    (bei aktivem Englisch tatsaechlich umbenannte) englische Layername
    gefunden werden - siehe translations.canonical_layer_name().
    """
    if not isinstance(group, QgsLayerTreeGroup):
        return None
    target = translations.canonical_layer_name(name)
    for node in group.children():
        try:
            lyr = node.layer()
        except Exception:
            lyr = None
        if isinstance(lyr, QgsVectorLayer) and translations.canonical_layer_name(lyr.name()) == target:
            return lyr
    return None


def _field_catalog_for_frm(frm_group: QgsLayerTreeGroup) -> list:
    """
    Liefert den Feld-Katalog eines Betriebs als sortierte Liste [(id, name), ...].

    Primärquelle: Felder-Layer (Felder.csv).
    Fallback / Ergänzung: IDs aus dem Feldgrenzen-Layer (für Altprojekte ohne
    Felder.csv bzw. falls eine Feldgrenze noch nicht registriert wurde).
    """
    catalog = {}

    felder_layer = _find_child_layer(frm_group, FELDER_LAYER_NAME)
    if felder_layer is not None:
        catalog.update(_felder_rows_from_layer(felder_layer))


    poly_layer = _find_child_layer(frm_group, "Feldgrenzen")
    if poly_layer is not None:
        fmap = _field_map(poly_layer)
        id_f = _pick_field(fmap, "ID")
        name_f = _pick_field(fmap, "Name")
        for feat in poly_layer.getFeatures():
            try:
                fid = int(feat[id_f]) if id_f else int(feat.id())
            except Exception:  # nosec B112
                continue
            if fid not in catalog or not catalog.get(fid):
                catalog[fid] = (str(feat[name_f]).strip() if name_f else "") or catalog.get(fid, "")

    return [(fid, catalog[fid]) for fid in sorted(catalog.keys())]


class AddFarmDialog(QDialog):
    def __init__(self, customers, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Betrieb hinzufügen"))
        self.setMinimumWidth(420)

        layout = QFormLayout(self)

        self.cmb_customer = QComboBox()
        self.cmb_customer.addItems(customers)

        self.edit_farm_name = QLineEdit()

        layout.addRow(_tr("Kunde auswählen:"), self.cmb_customer)
        layout.addRow(_tr("Betriebsname:"), self.edit_farm_name)

        crs_group = QGroupBox(_tr("KBS"))
        crs_row = QHBoxLayout(crs_group)

        self.rb_wgs84 = QRadioButton("WGS84 - EPSG:4326")
        self.rb_project = QRadioButton(_tr("Projekt-KBS"))
        self.rb_wgs84.setChecked(True)

        self.crs_buttons = QButtonGroup(self)
        self.crs_buttons.addButton(self.rb_wgs84)
        self.crs_buttons.addButton(self.rb_project)

        crs_row.addWidget(self.rb_wgs84)
        crs_row.addWidget(self.rb_project)
        crs_row.addStretch(1)

        layout.addRow(crs_group)

        btn_row = QHBoxLayout()
        self.btn_ok = QPushButton(_tr("OK"))
        self.btn_cancel = QPushButton(_tr("Abbrechen"))
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_ok)
        btn_row.addWidget(self.btn_cancel)

        layout.addRow(btn_row)

    def customer_name(self):
        return _norm_name(self.cmb_customer.currentText())

    def farm_name(self):
        return _norm_name(self.edit_farm_name.text())

    def selected_crs(self):
        if self.rb_project.isChecked():
            return QgsProject.instance().crs()
        return QgsCoordinateReferenceSystem("EPSG:4326")


class AddFieldDialog(QDialog):
    """Dialog zum Anlegen eines Feldes (Felder.csv) ohne Feldgrenze."""
    def __init__(self, farm_pairs, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Feld hinzufügen"))
        self.setMinimumWidth(420)
        self._pairs = list(farm_pairs)

        layout = QFormLayout(self)

        self.cmb_farm = QComboBox()
        for ctr, frm in self._pairs:
            self.cmb_farm.addItem(f"{ctr} / {frm}")

        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText(_tr("z.B. Hausacker"))

        layout.addRow(_tr("Betrieb:"), self.cmb_farm)
        layout.addRow(_tr("Feldname:"), self.edit_name)

        hint = QLabel(_tr(
            "Es wird ein Feld ohne Feldgrenze im Katalog (Felder.csv) angelegt.\n"
            "Die vergebene ID kannst du anschließend den Fahrspuren zuweisen."
        ))
        hint.setWordWrap(True)
        layout.addRow(hint)

        btn_row = QHBoxLayout()
        self.btn_ok = QPushButton(_tr("OK"))
        self.btn_cancel = QPushButton(_tr("Abbrechen"))
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_ok)
        btn_row.addWidget(self.btn_cancel)
        layout.addRow(btn_row)

    def selected_pair(self):
        i = self.cmb_farm.currentIndex()
        if 0 <= i < len(self._pairs):
            return self._pairs[i]
        return (None, None)

    def field_name(self):
        return _norm_name(self.edit_name.text())


class ToolboxDialog(QDialog):
    languageChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._i18n_entries = []  # (widget, key, setter) - fuer Live-Retranslation
        self.setWindowIcon(QIcon(":/isoxml/icons/logo.png"))
        self.setMinimumWidth(720)
        self._mk_text(self, "LK-Technik Path Planner", "setWindowTitle")

        logo_lbl = QLabel()
        pix = QPixmap(":/isoxml/icons/logo.png")
        if not pix.isNull():
            logo_lbl.setPixmap(pix.scaledToHeight(110, Qt.SmoothTransformation))
            self._mk_text(logo_lbl, "LK-Technik Path Planner", "setToolTip")

        self.mode_import = QRadioButton("Import")
        self.mode_export = QRadioButton("Export")
        self.mode_export.setChecked(True)

        # Sprach-Dropdown (oben rechts)
        self.cmb_lang = QComboBox()
        for code, label in translations.LANGUAGES.items():
            self.cmb_lang.addItem(label, code)
        idx = self.cmb_lang.findData(translations.get_language())
        if idx >= 0:
            self.cmb_lang.setCurrentIndex(idx)
        self.cmb_lang.currentIndexChanged.connect(self._on_language_selected)

        right_col = QVBoxLayout()
        right_col.addWidget(self.cmb_lang, 0, Qt.AlignRight)
        right_col.addWidget(logo_lbl, 0, Qt.AlignRight)

        top_row = QHBoxLayout()
        top_row.addWidget(self.mode_export)
        top_row.addWidget(self.mode_import)
        top_row.addStretch(1)
        top_row.addLayout(right_col)

        self.stack = QStackedWidget()
        self.page_export = self._build_export_page()
        self.page_import = self._build_import_page()
        self.stack.addWidget(self.page_export)
        self.stack.addWidget(self.page_import)

        self.mode_export.toggled.connect(self._sync_mode)
        self.mode_import.toggled.connect(self._sync_mode)
        self._sync_mode()

        root = QVBoxLayout(self)
        root.addLayout(top_row)
        root.addWidget(self.stack)
        btn_row = QHBoxLayout()
        self.run_button = self._mk_text(QPushButton(), "Ausführen")
        self.cancel_button = self._mk_text(QPushButton(), "Schließen")
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(self.run_button)
        btn_row.addWidget(self.cancel_button)
        root.addLayout(btn_row)
        self._updating_checks = False

    def _mk_text(self, widget, key, setter="setText"):
        """Setzt einen uebersetzbaren Text und merkt ihn fuer retranslate_ui() vor."""
        self._i18n_entries.append((widget, key, setter))
        getattr(widget, setter)(_tr(key))
        return widget

    def _on_language_selected(self, idx):
        code = self.cmb_lang.itemData(idx)
        if not code or code == translations.get_language():
            return
        translations.set_language(code)
        self.retranslate_ui()
        self.languageChanged.emit(code)

    def retranslate_ui(self):
        for widget, key, setter in self._i18n_entries:
            try:
                getattr(widget, setter)(_tr(key))
            except RuntimeError:
                pass
        self.tree.setHeaderLabels([_tr("Kunde / Betrieb / Feld")])
        self._refresh_terminal_format_label()

    def _sync_mode(self):
        self.stack.setCurrentIndex(0 if self.mode_export.isChecked() else 1)

    def _build_export_page(self):
        w = self._mk_text(QGroupBox(), "Export-Optionen", "setTitle")
        v = QVBoxLayout(w)

        # Output path
        path_row = QHBoxLayout()
        self.out_line = QLineEdit()
        btn = self._mk_text(QPushButton(), "…")
        def _pick_file():
            dn = QFileDialog.getExistingDirectory(
                self, _tr("Zielordner für Export wählen")
            )
            if dn:
                self.out_line.setText(dn)

        btn.clicked.connect(_pick_file)
        path_row.addWidget(self._mk_text(QLabel(), "Zielordner:"))
        path_row.addWidget(self.out_line, 1)
        path_row.addWidget(btn)
        v.addLayout(path_row)

        # Exportformat über Terminal-Auswahl (bestimmt das Dateiformat automatisch)
        opt_row = QHBoxLayout()
        opt_row.addWidget(self._mk_text(QLabel(), "Terminal:"))
        self.cmb_terminal = QComboBox()
        for brand, model, fmt in TERMINALS:
            self.cmb_terminal.addItem(f"{brand} – {model}", (brand, model, fmt))

        self.lbl_format = QLabel("")
        # Kontursegmente nur für Fendt One
        self.chk_seg = self._mk_text(QCheckBox(), "Kontursegmente")

        def _refresh_terminal_format_label():
            data = self.cmb_terminal.currentData()
            if not data:
                return
            brand, model, fmt = data
            self.lbl_format.setText(f"{_tr('Format:')} {_format_label(fmt)}")
            seg_visible = _is_fendt_one(brand, model)
            self.chk_seg.setVisible(seg_visible)
            if not seg_visible:
                self.chk_seg.setChecked(False)

        self._refresh_terminal_format_label = _refresh_terminal_format_label
        self.cmb_terminal.currentIndexChanged.connect(self._refresh_terminal_format_label)

        opt_row.addWidget(self.cmb_terminal, 1)
        opt_row.addWidget(self.lbl_format)
        opt_row.addWidget(self.chk_seg)
        opt_row.addStretch(1)
        v.addLayout(opt_row)

        # Vorauswahl: Standard-Terminal
        for i in range(self.cmb_terminal.count()):
            d = self.cmb_terminal.itemData(i)
            if d and d[0] == DEFAULT_TERMINAL[0] and d[1] == DEFAULT_TERMINAL[1]:
                self.cmb_terminal.setCurrentIndex(i)
                break
        self._refresh_terminal_format_label()

        # Erweiterte Optionen (einklappbar)
        self.btn_adv_export = QToolButton()
        self._mk_text(self.btn_adv_export, "Erweiterte Einstellungen")
        self.btn_adv_export.setCheckable(True)
        self.btn_adv_export.setChecked(False)
        self.btn_adv_export.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_adv_export.setArrowType(Qt.RightArrow)

        self.adv_export_widget = QWidget()
        adv_layout = QFormLayout(self.adv_export_widget)
        adv_layout.setContentsMargins(24, 4, 4, 4)

        self.chk_densify_curves = self._mk_text(QCheckBox(), "Kurven nach Intervall verdichten")
        self.spin_densify_interval = QDoubleSpinBox()
        self.spin_densify_interval.setDecimals(2)
        self.spin_densify_interval.setRange(0.10, 1000.0)
        self.spin_densify_interval.setSingleStep(0.50)
        self.spin_densify_interval.setValue(3.0)
        self.spin_densify_interval.setSuffix(" m")
        self.spin_densify_interval.setEnabled(False)

        self.chk_densify_curves.toggled.connect(self.spin_densify_interval.setEnabled)

        densify_row = QHBoxLayout()
        densify_row.addWidget(self.chk_densify_curves)
        densify_row.addWidget(self.spin_densify_interval)
        densify_row.addStretch(1)

        adv_layout.addRow(densify_row)

        self.chk_extend_curves = self._mk_text(QCheckBox(), "Kurven an den Enden verlängern")
        self.spin_extend_curves = QDoubleSpinBox()
        self.spin_extend_curves.setDecimals(2)
        self.spin_extend_curves.setRange(0.10, 1000.0)
        self.spin_extend_curves.setSingleStep(0.50)
        self.spin_extend_curves.setValue(15.0)
        self.spin_extend_curves.setSuffix(" m")
        self.spin_extend_curves.setEnabled(False)

        self.chk_extend_curves.toggled.connect(self.spin_extend_curves.setEnabled)
        extend_row = QHBoxLayout()
        extend_row.addWidget(self.chk_extend_curves)
        extend_row.addWidget(self.spin_extend_curves)
        extend_row.addStretch(1)

        adv_layout.addRow(extend_row)

        self.adv_export_widget.setVisible(False)

        def _toggle_adv_export(checked):
            self.adv_export_widget.setVisible(checked)
            self.btn_adv_export.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

        self.btn_adv_export.toggled.connect(_toggle_adv_export)

        v.addWidget(self.btn_adv_export)
        v.addWidget(self.adv_export_widget)

        # CTR→FRM→Felder tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_tr("Kunde / Betrieb / Feld")])
        self.tree.setColumnCount(1)
        self.tree.setSelectionMode(QTreeWidget.NoSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        v.addWidget(self._mk_text(QLabel(), "Wähle, was exportiert werden soll:"))
        v.addWidget(self.tree, 1)
        # Buttons: Kunde / Betrieb / Feld hinzufügen
        add_row = QHBoxLayout()
        self.btn_add_ctr = self._mk_text(QPushButton(), "Kunde hinzufügen")
        self.btn_add_frm = self._mk_text(QPushButton(), "Betrieb hinzufügen")
        self.btn_add_field = self._mk_text(QPushButton(), "Feld hinzufügen")
        add_row.addWidget(self.btn_add_ctr)
        add_row.addWidget(self.btn_add_frm)
        add_row.addWidget(self.btn_add_field)
        add_row.addStretch(1)
        v.addLayout(add_row)

        return w

    def _build_import_page(self):
        w = self._mk_text(QGroupBox(), "Import-Optionen", "setTitle")
        lay = QFormLayout(w)

        self.in_line = QLineEdit()

        btn_file = self._mk_text(QPushButton(), "Datei…")
        btn_folder = self._mk_text(QPushButton(), "Ordner…")

        def _pick_in_file():
            fn, _ = QFileDialog.getOpenFileName(
                self,
                _tr("TASKDATA.XML oder MasterData.xml wählen"),
                '',
                _tr('XML (*.xml);;Alle Dateien (*)')
            )
            if fn:
                self.in_line.setText(fn)

        def _pick_in_folder():
            dn = QFileDialog.getExistingDirectory(
                self,
                _tr("Ordner wählen (Gen4 / AgGPS / ISOXML)")
            )
            if dn:
                self.in_line.setText(dn)

        btn_file.clicked.connect(_pick_in_file)
        btn_folder.clicked.connect(_pick_in_folder)

        h1 = QHBoxLayout()
        h1.addWidget(self.in_line, 1)
        h1.addWidget(btn_file)
        h1.addWidget(btn_folder)

        lay.addRow(self._mk_text(QLabel(), "TASKDATA.XML, Gen4- oder AgGPS-Ordner:"), h1)
        self.out_dir_line = QLineEdit()
        btn_dir = self._mk_text(QPushButton(), "…")
        def _pick_dir():
            dn = QFileDialog.getExistingDirectory(self, _tr("Ausgabe-Ordner (optional)"))
            if dn:
                self.out_dir_line.setText(dn)
        btn_dir.clicked.connect(_pick_dir)
        h2 = QHBoxLayout(); h2.addWidget(self.out_dir_line, 1); h2.addWidget(btn_dir)
        lay.addRow(self._mk_text(QLabel(), "Ausgabe Ordner (GPKG, optional):"), h2)

        #CRS-Auswahl
        crs_group = self._mk_text(QGroupBox(), "Koordinatensystem für GPKG (Import)", "setTitle")
        crs_row = QHBoxLayout(crs_group)
        self.rb_import_wgs84 = QRadioButton("WGS 84 – EPSG:4326")
        self.rb_import_project = self._mk_text(QRadioButton(), "Projekt-KBS")
        self.rb_import_wgs84.setChecked(True)  # Default
        self._mk_text(self.rb_import_wgs84, "Geometrien als WGS84 speichern (empfohlen).", "setToolTip")
        self._mk_text(self.rb_import_project, "Geometrien ins aktuelle Projekt-KBS transformieren und so speichern.", "setToolTip")
        crs_row.addWidget(self.rb_import_wgs84)
        crs_row.addWidget(self.rb_import_project)
        crs_row.addStretch(1)
        lay.addRow(crs_group)

        lay.addRow(self._mk_text(
            QLabel(),
            "Hinweis: Ohne Ausgabe-Ordner werden die Layer als Temporärlayer geladen und können nicht direkt wieder exportiert werden!"
        ))
        return w

    def refresh_tree(self):
        self.tree.clear()
        root = QgsProject.instance().layerTreeRoot()
        for ctr_node in root.children():
            if not isinstance(ctr_node, QgsLayerTreeGroup):
                continue
            ctr_item = QTreeWidgetItem([ctr_node.name()])
            ctr_item.setFlags(ctr_item.flags() | Qt.ItemIsUserCheckable)
            ctr_item.setCheckState(0, Qt.Checked)
            self.tree.addTopLevelItem(ctr_item)
            for frm_node in ctr_node.children():
                if not isinstance(frm_node, QgsLayerTreeGroup):
                    continue
                frm_item = QTreeWidgetItem([frm_node.name()])
                frm_item.setFlags(frm_item.flags() | Qt.ItemIsUserCheckable)
                frm_item.setCheckState(0, Qt.Checked)
                ctr_item.addChild(frm_item)
                # Felder aus dem Katalog (Felder.csv) statt nur aus Feldgrenzen.
                # Dadurch erscheinen auch Felder ohne Feldgrenze (z.B. nur Fahrspuren).
                catalog = _field_catalog_for_frm(frm_node)
                if not catalog:
                    continue

                for stored_id, label_name in catalog:
                    label = label_name if label_name else str(stored_id)
                    item = QTreeWidgetItem([label])
                    item.setData(0, Qt.UserRole, int(stored_id))
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Checked)
                    frm_item.addChild(item)
        self.tree.expandAll()

    def selected_terminal(self):
        """Liefert (Marke, Bezeichnung, Format) des gewählten Terminals."""
        d = self.cmb_terminal.currentData()
        if d:
            return d
        return (None, None, None)

    def selected_export_map(self):
        res = {}
        for i in range(self.tree.topLevelItemCount()):
            ctr_item = self.tree.topLevelItem(i)
            if ctr_item.checkState(0) == Qt.Unchecked:
                continue
            ctr_name = ctr_item.text(0)
            frm_map = {}
            for j in range(ctr_item.childCount()):
                frm_item = ctr_item.child(j)
                if frm_item.checkState(0) == Qt.Unchecked:
                    continue
                frm_name = frm_item.text(0)
                field_ids = set()
                any_child_checked = False
                for k in range(frm_item.childCount()):
                    fld_item = frm_item.child(k)
                    if fld_item.checkState(0) != Qt.Unchecked:
                        any_child_checked = True
                        fid = fld_item.data(0, Qt.UserRole)
                        if fid is not None:
                            field_ids.add(int(fid))
                if frm_item.childCount() == 0:
                    continue
                if not any_child_checked:
                    field_ids = None
                frm_map[frm_name] = field_ids
            if frm_map:
                res[ctr_name] = frm_map
        return res

    def _set_checkstate_recursive(self, item: QTreeWidgetItem, state: Qt.CheckState):
        """Setzt CheckState für item + alle Kinder rekursiv."""
        item.setCheckState(0, state)
        for i in range(item.childCount()):
            self._set_checkstate_recursive(item.child(i), state)
    
    def _set_parent_checked(self, item: QTreeWidgetItem):
        """Setzt alle Eltern des Items auf Checked (damit Export-Auswahl nicht leer ist)."""
        p = item.parent()
        while p is not None:
            if p.checkState(0) != Qt.Checked:
                p.setCheckState(0, Qt.Checked)
            p = p.parent()

    def _update_parent_state_from_children(self, item: QTreeWidgetItem):
        """
        Optional: Elternstatus an Kinder anpassen.
        - alle Kinder Checked => Parent Checked
        - alle Kinder Unchecked => Parent Unchecked
        - gemischt => Parent PartiallyChecked
        """
        p = item.parent()
        while p is not None:
            checked = 0
            unchecked = 0
            for i in range(p.childCount()):
                st = p.child(i).checkState(0)
                if st == Qt.Checked:
                    checked += 1
                elif st == Qt.Unchecked:
                    unchecked += 1
                else:
                    # PartiallyChecked zählt als gemischt
                    checked += 1
                    unchecked += 1

            if checked == p.childCount():
                new_state = Qt.Checked
            elif unchecked == p.childCount():
                new_state = Qt.Unchecked
            else:
                new_state = Qt.PartiallyChecked

            if p.checkState(0) == new_state:
                break

            p.setCheckState(0, new_state)
            p = p.parent()

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        if column != 0:
            return
        if self._updating_checks:
            return

        self._updating_checks = True
        try:
            state = item.checkState(0)

            # Fall A: Kunde/Betrieb geklickt (hat Kinder) -> rekursiv auf Kinder anwenden
            if item.childCount() > 0:
                for i in range(item.childCount()):
                    self._set_checkstate_recursive(item.child(i), state)
                # optional Eltern darüber aktualisieren (falls Betrieb unter Kunde)
                self._update_parent_state_from_children(item)
                return

            # Fall B: Feld (Leaf) geklickt -> Eltern automatisch setzen
            if state == Qt.Checked:
                # sobald ein Feld aktiv ist -> Betrieb + Kunde aktiv
                self._set_parent_checked(item)
            else:
                # optional: wenn Feld abgewählt wird, Elternstatus sauber nachziehen
                self._update_parent_state_from_children(item)

        finally:
            self._updating_checks = False

class LkTechnikPathPlanner:
    def __init__(self, iface):
        self.iface = iface
        translations.load_saved_language()
        self.actions = []
        self.menu = _tr('&LK-Technik Path Planner')
        self.first_start = True
        # Felder.csv-Automatik
        self._wired_feldgrenzen = set()   # Layer-IDs mit verbundenem Commit-Signal
        self._felder_guard = False        # Re-Entrancy-Schutz beim Zurückschreiben der ID

    def tr(self, m):
        return _tr(m)

    def _style_file_for_layer(self, layer_name: str) -> str:
        """
        Liefert den Pfad zur passenden QML-Datei im Plugin-Unterordner styles.
        """
        style_map = {
            "Feldgrenzen": "Feldgrenzen.qml",
            "Flaechenhindernis": "Flaechenhindernis.qml",
            "Punkthindernis": "Punkthindernis.qml",
            "Fahrspuren": "Fahrspuren.qml",
        }

        filename = style_map.get(translations.canonical_layer_name(layer_name))
        if not filename:
            return ""

        plugin_dir = os.path.dirname(__file__)
        style_path = os.path.join(plugin_dir, "styles", filename)
        return style_path if os.path.exists(style_path) else ""

    def _apply_predefined_style(self, layer: QgsVectorLayer):
        """
        Wendet den vordefinierten Style anhand des exakten Layernamens an.
        """
        if not layer or not isinstance(layer, QgsVectorLayer):
            return

        style_path = self._style_file_for_layer(layer.name())
        if not style_path:
            return

        try:
            result = layer.loadNamedStyle(style_path)

            ok = True
            msg = ""

            if isinstance(result, tuple):
                # QGIS liefert meist: (msg, ok)
                if len(result) >= 2:
                    msg = result[0]
                    ok = result[1]
                elif len(result) == 1:
                    msg = str(result[0])

            elif isinstance(result, bool):
                ok = result

            elif result is not None:
                msg = str(result)

            if not ok:
                self.iface.messageBar().pushMessage(
                    _tr("Style-Warnung"),
                    _tr("Style für Layer '{name}' konnte nicht geladen werden: {msg}").format(
                        name=_display_layer_name(layer.name()), msg=msg),
                    level=Qgis.Warning,
                    duration=4
                )

            layer.triggerRepaint()

            try:
                self.iface.layerTreeView().refreshLayerSymbology(layer.id())
            except Exception:  # nosec B110
                pass

        except Exception as e:
            self.iface.messageBar().pushMessage(
                _tr("Style-Fehler"),
                _tr("Fehler beim Laden des Styles für '{name}': {e}").format(
                    name=_display_layer_name(layer.name()), e=e),
                level=Qgis.Warning,
                duration=4
            )
    def _betrieb_palette(self):
        return [
            QColor(230, 57, 70),    # rot
            QColor(46, 125, 50),    # grün
            QColor(245, 158, 11),   # orange
            QColor(123, 31, 162),   # violett
            QColor(0, 121, 107),    # türkis
            QColor(198, 40, 40),    # dunkelrot
            QColor(2, 136, 209),    # hellblau
            QColor(124, 179, 66),   # hellgrün
            QColor(255, 112, 67),   # koralle
            QColor(94, 53, 177),    # lila
            QColor(109, 76, 65),    # braun
        ]

    def _color_for_frm_group(self, frm_group: QgsLayerTreeGroup) -> QColor:
        """
        Vergibt die Farbe anhand der Position des Betriebs unter ALLEN Kunden
        im Projekt (nicht nur innerhalb des eigenen Kunden), damit auch
        Betriebe unterschiedlicher Kunden unterscheidbare Farben bekommen.
        """
        palette = self._betrieb_palette()

        if not isinstance(frm_group, QgsLayerTreeGroup):
            return palette[0]

        root = QgsProject.instance().layerTreeRoot()

        all_farm_groups = []
        for ctr in root.children():
            if not isinstance(ctr, QgsLayerTreeGroup):
                continue
            farms = [ch for ch in ctr.children() if isinstance(ch, QgsLayerTreeGroup)]
            all_farm_groups.extend(farms)

        # stabil sortieren nach Kunde + Betrieb, damit die Zuordnung
        # unabhängig von der Reihenfolge im Layerbaum immer gleich bleibt
        all_farm_groups = sorted(
            all_farm_groups,
            key=lambda g: (
                _norm_name(g.parent().name()).lower() if isinstance(g.parent(), QgsLayerTreeGroup) else "",
                _norm_name(g.name()).lower(),
            )
        )

        for idx, grp in enumerate(all_farm_groups):
            if grp == frm_group:
                return palette[idx % len(palette)]

        return palette[0]

    def _apply_feldgrenzen_color(self, layer: QgsVectorLayer, frm_group: QgsLayerTreeGroup):
        """
        Überschreibt nur bei Feldgrenzen die Füllfarbe des Styles.
        Die Farbe wird aus der Position des Betriebs innerhalb des Kunden vergeben.
        """
        if not layer or translations.canonical_layer_name(layer.name()) != "Feldgrenzen":
            return

        try:
            renderer = layer.renderer()
            if renderer is None:
                return

            symbol = renderer.symbol()
            if symbol is None:
                return

            fill_color = self._color_for_frm_group(frm_group)

            for i in range(symbol.symbolLayerCount()):
                sl = symbol.symbolLayer(i)
                if hasattr(sl, "setFillColor"):
                    sl.setFillColor(fill_color)

            layer.triggerRepaint()

            try:
                self.iface.layerTreeView().refreshLayerSymbology(layer.id())
            except Exception:  # nosec B110
                pass

        except Exception as e:
            self.iface.messageBar().pushMessage(
                _tr("Farb-Fehler"),
                _tr("Farbe für Feldgrenzen konnte nicht gesetzt werden: {e}").format(e=e),
                level=Qgis.Warning,
                duration=4
            )

    # ------------------- Mehrsprachigkeit -------------------
    def _apply_language_to_layer(self, node, layer: QgsVectorLayer):
        """
        Benennt den Layer passend zur aktuell gewählten Sprache um (echte
        Umbenennung - QGIS kennt fuer geladene Layer keinen rein kosmetischen
        Anzeigenamen unabhängig von layer.name()) und setzt die Feld-Aliase
        in der Attributtabelle. Alle internen Suchen nach Layern (siehe
        _find_child_layer) laufen über translations.canonical_layer_name()
        und finden den Layer daher unabhängig von der aktuellen Sprache.
        layer.source() (GPKG-Pfad/Tabellenname), Felder.csv und die .qml-
        Stylenamen bleiben unberührt, da sie nicht an layer.name() hängen.
        """
        lang = translations.get_language()
        try:
            target_name = translations.display_layer_name(layer.name(), lang)
            if layer.name() != target_name:
                layer.setName(target_name)
        except Exception:  # nosec B110
            pass
        try:
            alias_map = translations.FIELD_ALIASES.get(lang, {})
            for idx, f in enumerate(layer.fields()):
                layer.setFieldAlias(idx, alias_map.get(f.name(), ""))
        except Exception:  # nosec B110
            pass
        try:
            # ValueMap-Widget (z.B. "befahrbar": 0/1) mit uebersetzten
            # Beschriftungen neu setzen - die .qml-Styles legen es zunaechst
            # nur auf Deutsch an.
            value_maps = translations.FIELD_VALUE_MAPS.get(lang, {})
            for field_name, value_map in value_maps.items():
                idx = layer.fields().indexOf(field_name)
                if idx >= 0:
                    layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("ValueMap", {"map": value_map}))
        except Exception:  # nosec B110
            pass

    def _apply_language_to_project(self):
        """Wendet die aktuelle Sprache (Layer-Namen + Feld-Aliase) auf alle geladenen Layer an."""
        root = QgsProject.instance().layerTreeRoot()
        for node in root.findLayers():
            lyr = node.layer()
            if isinstance(lyr, QgsVectorLayer):
                self._apply_language_to_layer(node, lyr)

    def _on_language_changed(self, code):
        self._apply_language_to_project()

    def _dedupe_felder_layers(self):
        """
        Sicherheitsnetz: Entfernt je Betrieb überzählige Felder-Katalog-Layer
        (Kanon-Name "Felder", unabhängig von der aktuell angezeigten Sprache).
        Es handelt sich um reine Read-only-Ansichten derselben Felder.csv,
        es geht also keine Dateninformation verloren.
        """
        root = QgsProject.instance().layerTreeRoot()
        to_remove = []
        for ctr in root.children():
            if not isinstance(ctr, QgsLayerTreeGroup):
                continue
            for frm in ctr.children():
                if not isinstance(frm, QgsLayerTreeGroup):
                    continue
                felder_layers = []
                for ch in frm.children():
                    try:
                        lyr = ch.layer()
                    except Exception:
                        lyr = None
                    if isinstance(lyr, QgsVectorLayer) and translations.canonical_layer_name(lyr.name()) == FELDER_LAYER_NAME:
                        felder_layers.append(lyr)
                for extra in felder_layers[1:]:
                    to_remove.append(extra.id())
        if to_remove:
            QgsProject.instance().removeMapLayers(to_remove)

    def _reorder_frm_group_layers(self, frm_group: QgsLayerTreeGroup):
        """
        Sortiert die Layer innerhalb einer Betriebsgruppe in die gewünschte Reihenfolge
        im Layerbaum:

        oben:
            Punkthindernis
            Flaechenhindernis
            Fahrspuren
            Feldgrenzen
        unten

        Dadurch wird Feldgrenzen zeichnerisch ganz unten dargestellt.
        """
        if not isinstance(frm_group, QgsLayerTreeGroup):
            return

        desired_order = [
            "Felder",
            "Punkthindernis",
            "Flaechenhindernis",
            "Fahrspuren",
            "Feldgrenzen",
        ]

        layer_nodes = []
        for ch in frm_group.children():
            try:
                lyr = ch.layer()
            except Exception:
                lyr = None
            if isinstance(lyr, QgsVectorLayer):
                layer_nodes.append((translations.canonical_layer_name(lyr.name()), ch))

        name_to_node = {name: node for name, node in layer_nodes}

        insert_pos = 0
        for layer_name in desired_order:
            node = name_to_node.get(layer_name)
            if node is None:
                continue
            current_pos = frm_group.children().index(node)
            if current_pos != insert_pos:
                clone = node.clone()
                frm_group.insertChildNode(insert_pos, clone)
                frm_group.removeChildNode(node)
            insert_pos += 1

    def add_action(self, icon_path, text, callback, parent=None):
        action = QAction(QIcon(icon_path) if icon_path else QIcon(), text, parent)
        action.triggered.connect(callback)
        self.iface.addToolBarIcon(action)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        self.add_action(':/isoxml/icons/logo.png',
                        text=self.tr('LK-Technik Path Planner (Import/Export)'),
                        callback=self.run,
                        parent=self.iface.mainWindow())

        try:
            QgsProject.instance().layersAdded.connect(self._on_layers_added)
        except Exception:  # nosec B110
            pass
        try:
            QgsProject.instance().readProject.connect(self._on_project_read)
        except Exception:  # nosec B110
            pass
        # bereits geladenes Projekt (Plugin nach Projektöffnung aktiviert):
        # Feldgrenzen-Signal verdrahten und Feld-Dropdowns sofort einrichten,
        # ohne dass der Path Planner dafür erst geöffnet werden muss.
        try:
            self._on_layers_added(list(QgsProject.instance().mapLayers().values()))
        except Exception:  # nosec B110
            pass
        try:
            self._sync_project_state()
        except Exception:  # nosec B110
            pass

    def unload(self):
        for a in self.actions:
            self.iface.removeToolBarIcon(a)
            self.iface.removePluginMenu(self.menu, a)
        try:
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
        except Exception:  # nosec B110
            pass
        try:
            QgsProject.instance().readProject.disconnect(self._on_project_read)
        except Exception:  # nosec B110
            pass

    def _on_project_read(self, *args):
        """Wird nach jedem vollständigen Laden eines Projekts ausgelöst."""
        try:
            self._sync_project_state()
        except Exception:  # nosec B110
            pass

    # ------------------- Felder.csv-Automatik -------------------
    def _on_layers_added(self, layers):
        for lyr in layers:
            try:
                if isinstance(lyr, QgsVectorLayer) and translations.canonical_layer_name(lyr.name()) == "Feldgrenzen":
                    self._wire_feldgrenzen_layer(lyr)
            except Exception:  # nosec B110
                pass
            try:
                if isinstance(lyr, QgsVectorLayer):
                    node = QgsProject.instance().layerTreeRoot().findLayer(lyr.id())
                    if node is not None:
                        self._apply_language_to_layer(node, lyr)
            except Exception:  # nosec B110
                pass

    def _wire_feldgrenzen_layer(self, layer: QgsVectorLayer):
        """Verbindet das Commit-Signal eines Feldgrenzen-Layers (einmalig)."""
        try:
            lid = layer.id()
        except Exception:
            return
        if lid in self._wired_feldgrenzen:
            return
        # nur datei-basierte Layer haben eine zugehörige Felder.csv
        if not _felder_csv_path_for_layer(layer):
            return
        try:
            layer.committedFeaturesAdded.connect(self._on_feldgrenzen_committed)
            self._wired_feldgrenzen.add(lid)
        except Exception:  # nosec B110
            pass

    def _on_feldgrenzen_committed(self, layer_id, added_features):
        """
        Wird ausgelöst, sobald neu gezeichnete Feldgrenzen gespeichert werden.
        Legt für jede neue Grenze einen Eintrag in Felder.csv an und vergibt
        fehlende IDs automatisch.
        """
        if self._felder_guard:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            return
        csv_path = _felder_csv_path_for_layer(layer)
        if not csv_path:
            return

        fmap = _field_map(layer)
        id_field = _pick_field(fmap, "ID")
        name_field = _pick_field(fmap, "Name")

        rows = _read_felder_csv(csv_path)

        # nächste freie ID aus Katalog UND vorhandenen Feldgrenzen ableiten
        max_id = max(rows.keys()) if rows else 0
        try:
            for feat in layer.getFeatures():
                v = feat[id_field] if id_field else None
                if not _is_nullish(v):
                    max_id = max(max_id, int(v))
        except Exception:  # nosec B110
            pass

        attr_changes = {}        # fid -> {attr_index: value}
        pending_new = []         # (fid, name) für NEU vergebene IDs
        changed = False

        for feat in added_features:
            # vorhandene ID lesen
            fid = None
            if id_field:
                try:
                    raw = feat[id_field]
                    if not _is_nullish(raw):
                        fid = int(raw)
                except Exception:
                    fid = None

            # Name bestimmen
            name = ""
            if name_field:
                try:
                    nv = feat[name_field]
                    if not _is_nullish(nv):
                        name = str(nv).strip()
                except Exception:
                    name = ""

            if fid is not None:
                # bereits zugeordnetes Feld -> direkt registrieren
                nm = name or f"Feld {fid}"
                if fid not in rows or not rows.get(fid):
                    rows[fid] = nm
                    changed = True
            else:
                # neue ID vergeben; Eintrag aber ERST nach erfolgreichem
                # Zurückschreiben anlegen (sonst Doppel-Anlage über den Sync).
                max_id += 1
                fid = max_id
                nm = name or f"Feld {fid}"
                idx = layer.fields().indexOf(id_field) if id_field else -1
                if idx >= 0:
                    attr_changes[feat.id()] = {idx: fid}
                    pending_new.append((fid, nm))

        # IDs in die Feldgrenzen zurückschreiben (Provider-Ebene, ohne neues Commit-Signal)
        if attr_changes:
            ok = False
            self._felder_guard = True
            try:
                ok = bool(layer.dataProvider().changeAttributeValues(attr_changes))
                layer.reload()
                layer.triggerRepaint()
            except Exception:
                ok = False
            finally:
                self._felder_guard = False

            if ok:
                for fid, nm in pending_new:
                    if fid not in rows or not rows.get(fid):
                        rows[fid] = nm
                        changed = True
            # bei Fehlschlag: KEINE Katalogzeile -> der nächste Sync (Ebene dann
            # nicht mehr im Edit-Modus) registriert das Feld genau einmal.

        if not changed:
            return

        _write_felder_csv(csv_path, rows)
        self._reload_felder_for_feldgrenzen(layer, csv_path)

        # Auswahlbaum aktualisieren, falls Dialog offen
        try:
            if getattr(self, "dlg", None) is not None:
                self.dlg.refresh_tree()
        except Exception:  # nosec B110
            pass

    def _reload_felder_for_feldgrenzen(self, feldgrenzen_layer: QgsVectorLayer, csv_path: str):
        """Lädt den Felder-Layer der zugehörigen Gruppe neu (oder legt ihn an)."""
        project = QgsProject.instance()
        node = project.layerTreeRoot().findLayer(feldgrenzen_layer.id())
        parent = node.parent() if node is not None else None
        if not isinstance(parent, QgsLayerTreeGroup):
            return

        felder_layer = _find_child_layer(parent, FELDER_LAYER_NAME)
        if felder_layer is not None:
            try:
                felder_layer.reload()
                felder_layer.triggerRepaint()
            except Exception:  # nosec B110
                pass
            return

        # Felder-Layer existiert noch nicht (Altprojekt) -> anlegen
        new_layer = _load_felder_layer(csv_path)
        if new_layer is not None:
            project.addMapLayer(new_layer, False)
            parent.insertLayer(0, new_layer)

    def _recreate_felder_layer(self, frm_group: QgsLayerTreeGroup, csv_path: str):
        """
        Aktualisiert den Felder-Layer der Gruppe aus der (gefüllten) CSV.

        WICHTIG: Der Layer wird NICHT entfernt und neu angelegt, sondern
        in-place neu geladen, damit seine Layer-ID stabil bleibt. Sonst zeigen
        die Value-Relation-Dropdowns der anderen Layer auf eine nicht mehr
        existierende Felder-Layer-ID ("… erfordert den Layer 'Felder' …").
        Nur wenn noch kein Felder-Layer existiert, wird er neu geladen.
        """
        project = QgsProject.instance()
        old = _find_child_layer(frm_group, FELDER_LAYER_NAME)
        if old is not None:
            try:
                old.dataProvider().reloadData()
                old.reload()
                old.updateExtents()
                old.triggerRepaint()
            except Exception:  # nosec B110
                pass
            return old
        new_layer = _load_felder_layer(csv_path)
        if new_layer is not None:
            project.addMapLayer(new_layer, False)
            frm_group.insertLayer(0, new_layer)
            try:
                self._reorder_frm_group_layers(frm_group)
            except Exception:  # nosec B110
                pass
        return new_layer

    def _sync_all_felder_catalogs(self):
        """
        Gleicht Felder.csv für JEDEN Betrieb mit dem zugehörigen
        Feldgrenzen-Layer ab. Wird beim Öffnen des Dialogs aufgerufen –
        ähnlich wie die Styles – damit neu gezeichnete Felder zuverlässig
        in den Katalog übernommen werden.
        """
        root = QgsProject.instance().layerTreeRoot()
        for ctr_node in root.children():
            if not isinstance(ctr_node, QgsLayerTreeGroup):
                continue
            for frm_node in ctr_node.children():
                if not isinstance(frm_node, QgsLayerTreeGroup):
                    continue
                try:
                    self._sync_felder_for_group(frm_node)
                except Exception:  # nosec B110
                    pass

    def _apply_field_dropdowns(self):
        """
        Konfiguriert das ID-Feld von Feldgrenzen/Fahrspuren/Hindernissen als
        Auswahl-Dropdown (Value Relation), das die Feldnamen aus dem Katalog
        (Felder) anzeigt und die id speichert. So muss man keine IDs kennen.
        Wird beim Öffnen NACH der Style-Anwendung aufgerufen, da der Style die
        Editor-Widgets sonst wieder überschreiben würde.
        """
        root = QgsProject.instance().layerTreeRoot()
        for ctr_node in root.children():
            if not isinstance(ctr_node, QgsLayerTreeGroup):
                continue
            for frm_node in ctr_node.children():
                if not isinstance(frm_node, QgsLayerTreeGroup):
                    continue
                try:
                    self._apply_field_dropdown_for_group(frm_node)
                except Exception:  # nosec B110
                    pass

    def _apply_field_dropdown_for_group(self, frm_group: QgsLayerTreeGroup):
        felder = _find_child_layer(frm_group, FELDER_LAYER_NAME)
        if felder is None:
            # Altprojekt: Felder-Layer fehlt evtl. -> aus CSV nachladen
            csv_path = ""
            for nm in ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"):
                lyr = _find_child_layer(frm_group, nm)
                p = _felder_csv_path_for_layer(lyr) if lyr else ""
                if p:
                    csv_path = p
                    break
            if csv_path:
                if not os.path.exists(csv_path):
                    _write_felder_csv(csv_path, {})
                new_layer = _load_felder_layer(csv_path)
                if new_layer is not None:
                    QgsProject.instance().addMapLayer(new_layer, False)
                    frm_group.insertLayer(0, new_layer)
                    felder = new_layer
        if felder is None:
            return
        # WICHTIG: KEINE Layer-ID ("Layer") speichern. Eine gespeicherte Layer-ID
        # erzeugt in QGIS eine harte Abhängigkeit; passt die ID nach Neuladen/
        # Re-Import/Projektöffnen nicht exakt, erscheint die Warnung
        # "Layer 'Feldgrenzen' erfordert den Layer 'Felder' …".
        # Über LayerSource/LayerName/LayerProviderName wird der Felder-Layer
        # zuverlässig aufgelöst (jede Betriebs-Felder.csv hat einen eindeutigen
        # Pfad) – ohne ID-Abhängigkeit.
        config = {
            "Layer": "",
            "LayerName": felder.name(),
            "LayerSource": felder.publicSource(),
            "LayerProviderName": felder.providerType(),
            "Key": "id",
            "Value": "Name",
            "AllowNull": True,
            "AllowMulti": False,
            "OrderByValue": False,      # nach Anzeigewert (Name) sortieren
            "NofColumns": 1,
            "UseCompleter": False,
            "FilterExpression": "",
        }
        setup = QgsEditorWidgetSetup("ValueRelation", config)

        for nm in ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"):
            lyr = _find_child_layer(frm_group, nm)
            if lyr is None:
                continue
            idf = _pick_field(_field_map(lyr), "ID")
            if not idf:
                continue
            idx = lyr.fields().indexOf(idf)
            if idx >= 0:
                try:
                    lyr.setEditorWidgetSetup(idx, setup)
                except Exception:  # nosec B110
                    pass

    def _sync_felder_for_group(self, frm_group: QgsLayerTreeGroup):
        """
        Merge -> Felder.csv für einen Betrieb:
        - vorhandene Katalog-Einträge (Import, auch ohne Grenze) bleiben erhalten
        - jede Feldgrenze ohne ID bekommt eine neue, eindeutige ID zugewiesen
        - IDs aus Fahrspuren / Punkthindernis / Flaechenhindernis werden als
          Felder registriert (so entstehen Felder OHNE Feldgrenze, nur mit Spuren)
        """
        poly_layer = _find_child_layer(frm_group, "Feldgrenzen")
        line_layer = _find_child_layer(frm_group, "Fahrspuren")
        pt_layer   = _find_child_layer(frm_group, "Punkthindernis")
        fh_layer   = _find_child_layer(frm_group, "Flaechenhindernis")

        # Felder.csv über irgendeinen datei-basierten Layer ermitteln
        csv_path = ""
        for cand in (poly_layer, line_layer, pt_layer, fh_layer):
            csv_path = _felder_csv_path_for_layer(cand) if cand else ""
            if csv_path:
                break
        if not csv_path:
            return  # nur Memory-Layer -> kein CSV vorhanden

        rows = _read_felder_csv(csv_path)

        # max_id über Katalog UND alle vorhandenen IDs bestimmen
        max_id = max(rows.keys()) if rows else 0
        for lyr in (poly_layer, line_layer, pt_layer, fh_layer):
            if lyr is None:
                continue
            idf = _pick_field(_field_map(lyr), "ID")
            if not idf:
                continue
            for f in lyr.getFeatures():
                v = f[idf]
                if not _is_nullish(v):
                    try:
                        max_id = max(max_id, int(v))
                    except Exception:  # nosec B110
                        pass

        changed = False

        # 1) Feldgrenzen: fehlende IDs vergeben + Namen registrieren
        #    NUR wenn die Ebene NICHT im Bearbeitungsmodus ist. Sonst sind neue
        #    Objekte noch nicht committet -> Provider-Schreiben würde fehlschlagen
        #    (OGR-Fehler) und das Feld würde beim späteren Speichern doppelt
        #    angelegt. Im Edit-Modus übernimmt das Speichern (Commit) bzw. das
        #    nächste Öffnen die Registrierung.
        if poly_layer is not None and not poly_layer.isEditable():
            fmap = _field_map(poly_layer)
            id_field = _pick_field(fmap, "ID")
            name_field = _pick_field(fmap, "Name")
            attr_changes = {}
            for feat in poly_layer.getFeatures():
                fid = None
                if id_field:
                    v = feat[id_field]
                    if not _is_nullish(v):
                        try:
                            fid = int(v)
                        except Exception:
                            fid = None
                if fid is None:
                    max_id += 1
                    fid = max_id
                    if id_field:
                        idx = poly_layer.fields().indexOf(id_field)
                        if idx >= 0:
                            attr_changes[feat.id()] = {idx: fid}
                # Feldname wird nur EINMAL gesetzt – durch die ERSTE Feldgrenze
                # (oder den Button "Feld hinzufügen"). Weitere Grenzen desselben
                # Feldes haben EIGENE Namen, die den Feldnamen NICHT verändern.
                bname = ""
                if name_field:
                    nv = feat[name_field]
                    if not _is_nullish(nv):
                        bname = str(nv).strip()
                if fid not in rows or not rows.get(fid):
                    rows[fid] = bname or f"Feld {fid}"
                    changed = True

            if attr_changes:
                self._felder_guard = True
                try:
                    poly_layer.dataProvider().changeAttributeValues(attr_changes)
                    poly_layer.reload()
                    poly_layer.triggerRepaint()
                except Exception:  # nosec B110
                    pass
                finally:
                    self._felder_guard = False
                changed = True

        # 2) Fahrspuren / Hindernisse: vorhandene IDs als Felder registrieren.
        #    (Kein Auto-Vergeben von IDs – eine Spur ohne ID bleibt unzugeordnet.)
        for lyr in (line_layer, pt_layer, fh_layer):
            if lyr is None:
                continue
            if lyr.isEditable():
                continue  # offene Bearbeitung -> erst nach dem Speichern erfassen
            idf = _pick_field(_field_map(lyr), "ID")
            if not idf:
                continue
            for f in lyr.getFeatures():
                v = f[idf]
                if _is_nullish(v):
                    continue
                try:
                    fid = int(v)
                except Exception:  # nosec B112
                    continue
                if fid not in rows:
                    rows[fid] = f"Feld {fid}"
                    changed = True

        if changed:
            _write_felder_csv(csv_path, rows)

        # Felder-Layer IMMER sicherstellen – auch wenn sich nichts geändert hat
        # (wichtig für Altprojekte, in denen der Felder-Layer noch nicht in der
        # Projektstruktur geladen ist; sonst fehlt die Quelle für das Dropdown).
        if not os.path.exists(csv_path):
            _write_felder_csv(csv_path, rows)
        ref_layer = poly_layer or line_layer or pt_layer or fh_layer
        if ref_layer is not None:
            self._reload_felder_for_feldgrenzen(ref_layer, csv_path)

    def run(self):
        if self.first_start:
            self.first_start = False
            self.dlg = ToolboxDialog(self.iface.mainWindow())
            self.dlg.run_button.clicked.connect(self._on_run)

            # Buttons nur EINMAL verbinden:
            self.dlg.btn_add_ctr.clicked.connect(self._ui_add_customer)
            self.dlg.btn_add_frm.clicked.connect(self._ui_add_farm)
            self.dlg.btn_add_field.clicked.connect(self._ui_add_field)
            self.dlg.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
            self.dlg.languageChanged.connect(self._on_language_changed)

        self._sync_project_state()
        self.dlg.refresh_tree()

        self.dlg.show()
        self.dlg.exec_()

    def _sync_project_state(self):
        """
        Voller Layer-Abgleich: Duplikate bereinigen, Felder.csv mit den
        Feldgrenzen abgleichen, Style/Sprache/Farbe je Layer anwenden und
        die ID-Felder als Feld-Dropdown (Value Relation) konfigurieren.

        Läuft nicht nur beim Öffnen des Path Planner-Dialogs (run()),
        sondern auch automatisch beim Öffnen eines Projekts (siehe
        _on_project_read in initGui) – sonst fehlt das Feld-Dropdown, wenn
        direkt nach dem Projektöffnen (ohne den Path Planner vorher zu
        öffnen) eine Feldgrenze angelegt wird.
        """
        # Sicherheitsnetz ZUERST: durch die fehlerhafte Vorversion entstandene
        # doppelte Felder-Katalog-Layer bereinigen, bevor irgendetwas anhand
        # von layer.name() gesucht wird.
        self._dedupe_felder_layers()

        # Felder.csv mit den Feldgrenzen abgleichen. Dadurch landen neu
        # gezeichnete Felder zuverlässig im Katalog, auch wenn das
        # Commit-Signal nicht gegriffen hat.
        self._sync_all_felder_catalogs()

        root = QgsProject.instance().layerTreeRoot()
        for node in root.findLayers():
            lyr = node.layer()
            if not isinstance(lyr, QgsVectorLayer):
                continue

            self._apply_predefined_style(lyr)
            self._apply_language_to_layer(node, lyr)

            if translations.canonical_layer_name(lyr.name()) == "Feldgrenzen":
                parent = node.parent()
                if isinstance(parent, QgsLayerTreeGroup):
                    self._apply_feldgrenzen_color(lyr, parent)

        # ID-Felder als Feld-Dropdown (Value Relation) konfigurieren.
        # Muss NACH der Style-Anwendung erfolgen, sonst überschreibt der Style
        # das Editor-Widget wieder.
        self._apply_field_dropdowns()

    def _on_run(self):
        if self.dlg.mode_export.isChecked():
            self._do_export()
        else:
            self._do_import()
    
    def _find_or_create_group(self, parent_group: QgsLayerTreeGroup, name: str) -> QgsLayerTreeGroup:
        name_n = _norm_name(name)
        for ch in parent_group.children():
            if isinstance(ch, QgsLayerTreeGroup) and _norm_name(ch.name()) == name_n:
                return ch
        return parent_group.addGroup(name_n)

    def _get_project_base_dir(self) -> str:
        """
        Ablageort für automatisch erzeugte GPKGs:
        - bevorzugt: QgsProject.homePath()
        - sonst: User wird gefragt
        """
        project = QgsProject.instance()
        base = (project.homePath() or "").strip()
        if base and os.path.isdir(base):
            return base

        dn = QFileDialog.getExistingDirectory(self.iface.mainWindow(), _tr("Ablageordner für neue Betriebe wählen"))
        return dn or ""

    def _ensure_frm_layers_on_disk(self, ctr_name: str, frm_name: str, frm_group: QgsLayerTreeGroup, target_crs=None):
        """
        Erstellt 4 leere Layer als GPKG und lädt sie in die Gruppe,
        falls sie dort noch nicht existieren.
        """
        project = QgsProject.instance()
        if target_crs is None or not target_crs.isValid():
            target_crs = QgsCoordinateReferenceSystem("EPSG:4326")

        crs_authid = target_crs.authid()

        base_dir = self._get_project_base_dir()
        if not base_dir:
            self.iface.messageBar().pushMessage(
                _tr("Abgebrochen"), _tr("Kein Ablageordner gewählt – Betrieb wurde nicht erstellt."),
                level=Qgis.Info, duration=4
            )
            return

        target_dir = os.path.join(base_dir, _safe(ctr_name), _safe(frm_name))
        os.makedirs(target_dir, exist_ok=True)

        # Ziel: keine Duplikate in der Gruppe
        existing_names = set()
        for ch in frm_group.children():
            try:
                lyr = ch.layer()
            except Exception:
                lyr = None
            if isinstance(lyr, QgsVectorLayer):
                existing_names.add(translations.canonical_layer_name(lyr.name()))

        def _write_empty_layer_to_gpkg(layer: QgsVectorLayer, gpkg_path: str, layername: str) -> QgsVectorLayer:
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = layername

            ret = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, gpkg_path, project.transformContext(), opts
            )

            # je nach QGIS-Version: ret kann (res, err) oder (res, err, newFileName, newLayerName, ...) sein
            res = ret[0] if isinstance(ret, (tuple, list)) else ret
            err = ret[1] if isinstance(ret, (tuple, list)) and len(ret) > 1 else ""

            if res != QgsVectorFileWriter.NoError:
                raise RuntimeError(err or f"Write error {res}")

            uri = f"{gpkg_path}|layername={layername}"
            file_layer = QgsVectorLayer(uri, layername, "ogr")
            if not file_layer.isValid():
                raise RuntimeError(f"Konnte Layer nicht laden: {uri}")
            return file_layer

        # 1) Feldgrenzen
        if "Feldgrenzen" not in existing_names:
            mem = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "Feldgrenzen", "memory")
            dp = mem.dataProvider()
            dp.addAttributes([
                QgsField("ID", QVariant.Int),
                QgsField("Name", QVariant.String),
                QgsField("Flaeche", QVariant.Double),
            ])
            mem.updateFields()

            gpkg = os.path.join(target_dir, "Feldgrenzen.gpkg")
            lyr = _write_empty_layer_to_gpkg(mem, gpkg, "Feldgrenzen")
            project.addMapLayer(lyr, False)
            frm_group.addLayer(lyr)
            self._apply_predefined_style(lyr)
            self._apply_feldgrenzen_color(lyr, frm_group)

        # 2) Fahrspuren
        if "Fahrspuren" not in existing_names:
            mem = QgsVectorLayer(f"MultiLineString?crs={crs_authid}", "Fahrspuren", "memory")
            dp = mem.dataProvider()
            dp.addAttributes([
                QgsField("ID", QVariant.Int),
                QgsField("Name", QVariant.String),
                QgsField("Segment", QVariant.String),
            ])
            mem.updateFields()

            gpkg = os.path.join(target_dir, "Fahrspuren.gpkg")
            lyr = _write_empty_layer_to_gpkg(mem, gpkg, "Fahrspuren")
            project.addMapLayer(lyr, False)
            frm_group.addLayer(lyr)
            self._apply_predefined_style(lyr)

        # 3) Punkthindernis
        if "Punkthindernis" not in existing_names:
            mem = QgsVectorLayer(f"Point?crs={crs_authid}", "Punkthindernis", "memory")
            dp = mem.dataProvider()
            dp.addAttributes([
                QgsField("ID", QVariant.Int),
                QgsField("Name", QVariant.String),
                QgsField("befahrbar", QVariant.Int),
            ])
            mem.updateFields()

            gpkg = os.path.join(target_dir, "Punkthindernis.gpkg")
            lyr = _write_empty_layer_to_gpkg(mem, gpkg, "Punkthindernis")
            project.addMapLayer(lyr, False)
            frm_group.addLayer(lyr)
            self._apply_predefined_style(lyr)

        # 4) Flaechenhindernis
        if "Flaechenhindernis" not in existing_names:
            mem = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "Flaechenhindernis", "memory")
            dp = mem.dataProvider()
            dp.addAttributes([
                QgsField("ID", QVariant.Int),
                QgsField("befahrbar", QVariant.Int),
            ])
            mem.updateFields()

            gpkg = os.path.join(target_dir, "Flaechenhindernis.gpkg")
            lyr = _write_empty_layer_to_gpkg(mem, gpkg, "Flaechenhindernis")
            project.addMapLayer(lyr, False)
            frm_group.addLayer(lyr)
            self._apply_predefined_style(lyr)

        # 5) Felder.csv (Feld-Katalog)
        if FELDER_LAYER_NAME not in existing_names:
            csv_path = _felder_csv_path_in_dir(target_dir)
            if not os.path.exists(csv_path):
                _write_felder_csv(csv_path, {})  # leere Datei mit Header
            felder_layer = _load_felder_layer(csv_path)
            if felder_layer is not None:
                project.addMapLayer(felder_layer, False)
                frm_group.addLayer(felder_layer)

        self._reorder_frm_group_layers(frm_group)

    def _ui_add_customer(self):
        name, ok = QInputDialog.getText(self.iface.mainWindow(), _tr("Kunde hinzufügen"), _tr("Kundenname:"))
        if not ok:
            return
        name = _norm_name(name)
        if not name:
            return

        root = QgsProject.instance().layerTreeRoot()
        _ = self._find_or_create_group(root, name)

        self.dlg.refresh_tree()
        self.iface.messageBar().pushMessage(
            _tr("OK"), _tr("Kunde '{name}' angelegt.").format(name=name), level=Qgis.Success, duration=3)

    def _ui_add_farm(self):
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        customers = [ch.name() for ch in root.children() if isinstance(ch, QgsLayerTreeGroup)]

        if not customers:
            QMessageBox.information(
                self.iface.mainWindow(),
                _tr("Hinweis"),
                _tr("Es gibt noch keinen Kunden. Bitte zuerst einen Kunden anlegen.")
            )
            return

        dlg = AddFarmDialog(customers, self.iface.mainWindow())

        if dlg.exec_() != QDialog.Accepted:
            return

        ctr_name = dlg.customer_name()
        frm_name = dlg.farm_name()
        target_crs = dlg.selected_crs()

        if not ctr_name or not frm_name:
            return

        ctr_group = self._find_or_create_group(root, ctr_name)
        frm_group = self._find_or_create_group(ctr_group, frm_name)

        try:
            self._ensure_frm_layers_on_disk(
                ctr_name,
                frm_name,
                frm_group,
                target_crs
            )
        except Exception as e:
            self.iface.messageBar().pushMessage(
                _tr("Fehler"),
                _tr("Konnte Betrieb/Layers nicht erstellen: {e}").format(e=e),
                level=Qgis.Critical,
                duration=6
            )
            return

        self.dlg.refresh_tree()
        self._apply_language_to_project()
        self._apply_field_dropdowns()

        self.iface.messageBar().pushMessage(
            _tr("OK"),
            _tr("Betrieb '{frm_name}' mit Layern erstellt ({crs}).").format(
                frm_name=frm_name, crs=target_crs.authid()),
            level=Qgis.Success,
            duration=4
        )

    def _ui_add_field(self):
        """
        Legt ein Feld ohne Feldgrenze direkt im Katalog (Felder.csv) an.
        So lassen sich Felder erstellen, die später NUR Fahrspuren haben.
        """
        root = QgsProject.instance().layerTreeRoot()

        pairs = []
        pair_groups = {}
        for ctr in root.children():
            if not isinstance(ctr, QgsLayerTreeGroup):
                continue
            for frm in ctr.children():
                if not isinstance(frm, QgsLayerTreeGroup):
                    continue
                key = (ctr.name(), frm.name())
                pairs.append(key)
                pair_groups[key] = frm

        if not pairs:
            QMessageBox.information(
                self.iface.mainWindow(),
                _tr("Hinweis"),
                _tr("Es gibt noch keinen Betrieb. Bitte zuerst einen Betrieb anlegen.")
            )
            return

        dlg = AddFieldDialog(pairs, self.iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return

        ctr_name, frm_name = dlg.selected_pair()
        name = dlg.field_name()
        if not ctr_name or not frm_name:
            return

        frm_group = pair_groups.get((ctr_name, frm_name))
        if frm_group is None:
            return

        # Felder.csv-Pfad über irgendeinen datei-basierten Layer der Gruppe ermitteln
        csv_path = ""
        for nm in ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"):
            lyr = _find_child_layer(frm_group, nm)
            p = _felder_csv_path_for_layer(lyr) if lyr else ""
            if p:
                csv_path = p
                break

        if not csv_path:
            QMessageBox.warning(
                self.iface.mainWindow(),
                _tr("Nicht möglich"),
                _tr("Die Layer dieses Betriebs sind noch temporär (nicht gespeichert).\n"
                    "Bitte zuerst dauerhaft als GeoPackage speichern, dann erneut versuchen.")
            )
            return

        rows = _read_felder_csv(csv_path)

        # nächste freie ID über Katalog + alle Layer bestimmen
        max_id = max(rows.keys()) if rows else 0
        for nm in ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"):
            lyr = _find_child_layer(frm_group, nm)
            if lyr is None:
                continue
            idf = _pick_field(_field_map(lyr), "ID")
            if not idf:
                continue
            for f in lyr.getFeatures():
                v = f[idf]
                if not _is_nullish(v):
                    try:
                        max_id = max(max_id, int(v))
                    except Exception:  # nosec B110
                        pass

        new_id = max_id + 1
        if not name:
            name = f"Feld {new_id}"

        rows[new_id] = name
        _write_felder_csv(csv_path, rows)

        ref_layer = _find_child_layer(frm_group, "Feldgrenzen") or _find_child_layer(frm_group, "Fahrspuren") \
            or _find_child_layer(frm_group, "Punkthindernis") or _find_child_layer(frm_group, "Flaechenhindernis")
        if ref_layer is not None:
            self._reload_felder_for_feldgrenzen(ref_layer, csv_path)
            self._apply_language_to_project()

        self.dlg.refresh_tree()
        self.iface.messageBar().pushMessage(
            _tr("OK"),
            _tr("Feld '{name}' angelegt (ID {new_id}). Weise diese ID den Fahrspuren zu.").format(
                name=name, new_id=new_id),
            level=Qgis.Success,
            duration=7
        )

    def _on_tree_context_menu(self, pos):
        """Rechtsklick auf ein Feld im Export-Baum -> Umbenennen / Löschen."""
        tree = self.dlg.tree
        item = tree.itemAt(pos)
        if item is None:
            return
        fid = item.data(0, Qt.UserRole)
        parent = item.parent()
        # nur Feld-Blätter haben eine ID und liegen unter Betrieb unter Kunde
        if fid is None or parent is None or parent.parent() is None:
            return
        frm_name = parent.text(0)
        ctr_name = parent.parent().text(0)

        menu = QMenu()
        act_rename = menu.addAction(_tr("Feld umbenennen…"))
        act_delete = menu.addAction(_tr("Feld löschen…"))
        chosen = menu.exec_(tree.viewport().mapToGlobal(pos))
        if chosen == act_rename:
            try:
                self._rename_field(ctr_name, frm_name, int(fid), item.text(0))
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    _tr("Fehler"), _tr("Umbenennen fehlgeschlagen: {e}").format(e=e), level=Qgis.Critical, duration=6
                )
        elif chosen == act_delete:
            try:
                self._delete_field(ctr_name, frm_name, int(fid), item.text(0))
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    _tr("Fehler"), _tr("Löschen fehlgeschlagen: {e}").format(e=e), level=Qgis.Critical, duration=6
                )

    def _delete_field(self, ctr_name: str, frm_name: str, field_id: int, current_name: str):
        """
        Löscht ein komplettes Feld: alle Objekte mit dieser ID aus Feldgrenzen,
        Fahrspuren, Punkthindernis und Flaechenhindernis sowie den Eintrag in
        Felder.csv. Mit Sicherheitsabfrage.
        """
        # Betriebsgruppe finden
        root = QgsProject.instance().layerTreeRoot()
        frm_group = None
        for ctr in root.children():
            if isinstance(ctr, QgsLayerTreeGroup) and ctr.name() == ctr_name:
                for frm in ctr.children():
                    if isinstance(frm, QgsLayerTreeGroup) and frm.name() == frm_name:
                        frm_group = frm
                        break
                break
        if frm_group is None:
            return

        layer_names = ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis")

        # Felder.csv-Pfad ermitteln
        csv_path = ""
        for nm in layer_names:
            lyr = _find_child_layer(frm_group, nm)
            p = _felder_csv_path_for_layer(lyr) if lyr else ""
            if p:
                csv_path = p
                break
        if not csv_path:
            QMessageBox.warning(
                self.iface.mainWindow(),
                _tr("Nicht möglich"),
                _tr("Die Layer dieses Betriebs sind noch temporär (nicht gespeichert).")
            )
            return

        # Ebenen im Bearbeitungsmodus blockieren das Löschen über den Provider
        editing = [nm for nm in layer_names
                   if (_find_child_layer(frm_group, nm) is not None
                       and _find_child_layer(frm_group, nm).isEditable())]
        if editing:
            QMessageBox.warning(
                self.iface.mainWindow(),
                _tr("Bearbeitung aktiv"),
                _tr("Bitte zuerst den Bearbeitungsmodus schließen für: {names}.").format(
                    names=", ".join(_display_layer_name(nm) for nm in editing))
            )
            return

        # Wie viele Objekte sind betroffen? (für die Abfrage)
        counts = {}
        fids_by_layer = {}
        for nm in layer_names:
            lyr = _find_child_layer(frm_group, nm)
            if lyr is None:
                continue
            idf = _pick_field(_field_map(lyr), "ID")
            if not idf:
                continue
            fids = []
            for f in lyr.getFeatures():
                v = f[idf]
                if _is_nullish(v):
                    continue
                try:
                    if int(v) == int(field_id):
                        fids.append(f.id())
                except Exception:  # nosec B112
                    continue
            if fids:
                counts[nm] = len(fids)
                fids_by_layer[nm] = fids

        label = current_name or _tr("Feld {field_id}").format(field_id=field_id)
        detail = "\n".join(
            _tr("  • {name}: {count} Objekt(e)").format(name=_display_layer_name(nm), count=counts[nm])
            for nm in layer_names if nm in counts)
        if not detail:
            detail = _tr("  • (keine Geometrien – nur Katalogeintrag)")

        msg = QMessageBox(self.iface.mainWindow())
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle(_tr("Feld löschen"))
        msg.setText(_tr("Sind Sie sicher, dass Sie das Feld „{label}“ (ID {field_id}) löschen möchten?").format(
            label=label, field_id=field_id))
        msg.setInformativeText(
            _tr("Damit werden ALLE Daten dieses Feldes unwiderruflich gelöscht – "
                "Feldgrenze(n), Fahrspuren, Hindernisse und der Katalogeintrag:\n\n")
            + detail
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.button(QMessageBox.Yes).setText(_tr("Ja, löschen"))
        msg.button(QMessageBox.No).setText(_tr("Abbrechen"))
        if msg.exec_() != QMessageBox.Yes:
            return

        # Objekte aus allen Ebenen entfernen
        self._felder_guard = True
        try:
            for nm, fids in fids_by_layer.items():
                lyr = _find_child_layer(frm_group, nm)
                if lyr is None or not fids:
                    continue
                try:
                    lyr.dataProvider().deleteFeatures(fids)
                    lyr.reload()
                    lyr.updateExtents()
                    lyr.triggerRepaint()
                except Exception:  # nosec B110
                    pass
        finally:
            self._felder_guard = False

        # Katalogeintrag entfernen
        rows = _read_felder_csv(csv_path)
        if field_id in rows:
            rows.pop(field_id, None)
            _write_felder_csv(csv_path, rows)

        ref_layer = None
        for nm in layer_names:
            ref_layer = _find_child_layer(frm_group, nm)
            if ref_layer is not None:
                break
        if ref_layer is not None:
            self._reload_felder_for_feldgrenzen(ref_layer, csv_path)
            self._apply_language_to_project()

        self.dlg.refresh_tree()
        self.iface.messageBar().pushMessage(
            _tr("OK"), _tr("Feld „{label}“ (ID {field_id}) wurde gelöscht.").format(
                label=label, field_id=field_id), level=Qgis.Success, duration=5
        )

    def _rename_field(self, ctr_name: str, frm_name: str, field_id: int, current_name: str):
        """
        Benennt ein Feld um: schreibt Felder.csv und gleicht – falls vorhanden –
        das Name-Attribut der zugehörigen Feldgrenze(n) an.
        Funktioniert auch für Felder OHNE Feldgrenze.
        """
        new_name, ok = QInputDialog.getText(
            self.iface.mainWindow(),
            _tr("Feld umbenennen"),
            _tr("Neuer Name für Feld (ID {field_id}):").format(field_id=field_id),
            text=current_name or ""
        )
        if not ok:
            return
        new_name = _norm_name(new_name)
        if not new_name:
            return

        # Betriebsgruppe finden
        root = QgsProject.instance().layerTreeRoot()
        frm_group = None
        for ctr in root.children():
            if isinstance(ctr, QgsLayerTreeGroup) and ctr.name() == ctr_name:
                for frm in ctr.children():
                    if isinstance(frm, QgsLayerTreeGroup) and frm.name() == frm_name:
                        frm_group = frm
                        break
                break
        if frm_group is None:
            return

        # Felder.csv-Pfad ermitteln
        csv_path = ""
        for nm in ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"):
            lyr = _find_child_layer(frm_group, nm)
            p = _felder_csv_path_for_layer(lyr) if lyr else ""
            if p:
                csv_path = p
                break
        if not csv_path:
            QMessageBox.warning(
                self.iface.mainWindow(),
                _tr("Nicht möglich"),
                _tr("Die Layer dieses Betriebs sind noch temporär (nicht gespeichert).")
            )
            return

        rows = _read_felder_csv(csv_path)
        rows[field_id] = new_name
        _write_felder_csv(csv_path, rows)

        # Hinweis: Die Namen der einzelnen Feldgrenzen werden bewusst NICHT
        # mitgeändert – sie sind eigenständig (mehrere Grenzen pro Feld können
        # unterschiedlich heißen). Umbenannt wird nur das Feld selbst (Katalog).

        ref_layer = _find_child_layer(frm_group, "Feldgrenzen") \
            or _find_child_layer(frm_group, "Fahrspuren") \
            or _find_child_layer(frm_group, "Punkthindernis") or _find_child_layer(frm_group, "Flaechenhindernis")
        if ref_layer is not None:
            self._reload_felder_for_feldgrenzen(ref_layer, csv_path)
            self._apply_language_to_project()

        self.dlg.refresh_tree()
        self.iface.messageBar().pushMessage(
            _tr("OK"), _tr("Feld (ID {field_id}) umbenannt in '{new_name}'.").format(
                field_id=field_id, new_name=new_name), level=Qgis.Success, duration=5
        )

    # ------------------------- IMPORT -------------------------
    def _do_import(self):
        path = self.dlg.in_line.text().strip()
        out_dir = self.dlg.out_dir_line.text().strip() or None

        if not path:
            self.iface.messageBar().pushMessage(
                _tr("Fehler"),
                _tr("Keine Datei oder kein Ordner gewählt."),
                level=Qgis.Warning,
                duration=4
            )
            return

        # John Deere Gen4 erkennen:
        # 1) Ordner gewählt -> direkt prüfen
        # 2) MasterData.xml gewählt -> Ordner darüber nehmen
        if os.path.isdir(path):
            gen4_master = os.path.join(path, "MasterData.xml")
            isoxml_taskdata = os.path.join(path, "TASKDATA.XML")

            # 0) AgGPS (Shapefile-Ordnerstruktur)
            if detect_aggps_data_root(path):
                ok = import_aggps(self, path, out_dir)
                if ok:
                    self._apply_language_to_project()
                    self.dlg.accept()
                return

            # 1) John Deere Gen4
            if os.path.exists(gen4_master):
                ok = import_john_deere_gen4(self, path, out_dir)
                if ok:
                    self._apply_language_to_project()
                    self.dlg.accept()
                return

            # 2) Klassisches ISOXML
            if os.path.exists(isoxml_taskdata):
                path = isoxml_taskdata
            else:
                self.iface.messageBar().pushMessage(
                    _tr("Fehler"),
                    _tr("Im gewählten Ordner wurde weder eine MasterData.xml noch eine TASKDATA.XML gefunden."),
                    level=Qgis.Warning,
                    duration=5
                )
                return

        elif os.path.isfile(path):
            base = os.path.basename(path).lower()
            if base == "masterdata.xml":
                gen4_dir = os.path.dirname(path)
                ok = import_john_deere_gen4(self, gen4_dir, out_dir)
                if ok:
                    self._apply_language_to_project()
                    self.dlg.accept()
                return

        if not path:
            self.iface.messageBar().pushMessage(_tr("Fehler"), _tr("Keine ISOXML-Datei gewählt."), level=Qgis.Warning, duration=4)
            return
        try:
            tree = _SafeET.parse(path)
            root = tree.getroot()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                _tr("Fehler"), _tr("XML-Parsing fehlgeschlagen: {e}").format(e=e), level=Qgis.Critical, duration=6)
            return
        is_v3 = (root.get("VersionMajor", "4") == "3")

        #CRS Auswahl & Transform
        src_crs = QgsCoordinateReferenceSystem("EPSG:4326")  # ISOXML ist immer WGS84
        use_project_crs = self.dlg.rb_import_project.isChecked()
        target_crs = QgsProject.instance().crs() if use_project_crs else src_crs
        to_target = QgsCoordinateTransform(src_crs, target_crs, QgsProject.instance())

        area_crs = None
        area_transform = None

        try:
            if target_crs.isValid() and target_crs.mapUnits() == Qgis.DistanceUnit.Meters:
                area_crs = target_crs
            else:
                area_crs = QgsCoordinateReferenceSystem("EPSG:32633")
            area_transform = QgsCoordinateTransform(src_crs, area_crs, QgsProject.instance())
        except Exception:
            area_crs = QgsCoordinateReferenceSystem("EPSG:32633")
            area_transform = QgsCoordinateTransform(src_crs, area_crs, QgsProject.instance())

        def _calc_area_from_ring_wgs84(ring_pts_wgs84):
            """
            Erwartet Ringpunkte in WGS84 (lon/lat als QgsPointXY).
            Berechnet Fläche in m² über metrisches CRS.
            """
            try:
                if len(ring_pts_wgs84) < 3:
                    return 0.0

                ring_metric = [area_transform.transform(pt) for pt in ring_pts_wgs84]
                geom_metric = QgsGeometry.fromPolygonXY([ring_metric])
                return float(geom_metric.area())
            except Exception:
                return 0.0

        def _tx_pt_xy(lon, lat):
            if target_crs == src_crs:
                return QgsPointXY(lon, lat)
            return to_target.transform(QgsPointXY(lon, lat))

        # CTR
        ctr_map = {}
        for ctr in root.findall('.//CTR'):
            ctr_id = ctr.get("A") or ctr.get("CTRId") or ctr.get("Id")
            ctr_name = ctr.get("B") or ctr.get("Designator") or (ctr_id or "CTR")
            if ctr_id:
                ctr_map[ctr_id] = ctr_name

        # FRM
        frm_map = {}
        for frm in root.findall('.//FRM'):
            frm_id = frm.get("A") or frm.get("FRMId") or frm.get("Id")
            frm_name = frm.get("B") or frm.get("Designator") or (frm_id or "FRM")
            ctr_ref = frm.get("I") or frm.get("CTRIdRef") or frm.get("C")
            if frm_id:
                frm_map[frm_id] = {"name": frm_name, "ctr": ctr_ref}

        project = QgsProject.instance()
        per_frm_layers = {}
        per_frm_groups = {}
        # Felder-Katalog je Betrieb
        per_frm_felder_rows = {}    # key -> {id: name}
        per_frm_felder_csv = {}     # key -> csv_pfad oder None (memory)
        per_frm_felder_layer = {}   # key -> Felder-Layer (delimitedtext oder memory)

        def _ensure_hierarchy(ctr_name: str, frm_name: str) -> QgsLayerTreeGroup:
            root_g = project.layerTreeRoot()
            ctr_grp = self._find_or_create_group(root_g, ctr_name)
            frm_grp = self._find_or_create_group(ctr_grp, frm_name)
            return frm_grp

        def _create_frm_layers():
            # CRS dynamic(WGS84 or Project-KBS)
            crs_authid = target_crs.authid() if target_crs.isValid() else "EPSG:4326"

            field_layer = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "Feldgrenzen", "memory")
            dp_field = field_layer.dataProvider()
            f_fields = QgsFields(); f_fields.append(QgsField("ID", QVariant.Int)); f_fields.append(QgsField("Name", QVariant.String)); f_fields.append(QgsField("Flaeche", QVariant.Double))
            dp_field.addAttributes(f_fields); field_layer.updateFields()

            line_layer = QgsVectorLayer(f"MultiLineString?crs={crs_authid}", "Fahrspuren", "memory")
            dp_line = line_layer.dataProvider()
            l_fields = QgsFields(); l_fields.append(QgsField("ID", QVariant.Int)); l_fields.append(QgsField("Name", QVariant.String)); l_fields.append(QgsField("Segment", QVariant.String))
            dp_line.addAttributes(l_fields); line_layer.updateFields()

            point_layer = QgsVectorLayer(f"Point?crs={crs_authid}", "Punkthindernis", "memory")
            dp_point = point_layer.dataProvider()
            p_fields = QgsFields(); p_fields.append(QgsField("ID", QVariant.Int)); p_fields.append(QgsField("Name", QVariant.String)); p_fields.append(QgsField("befahrbar", QVariant.Int))
            dp_point.addAttributes(p_fields); point_layer.updateFields()

            area_layer = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "Flaechenhindernis", "memory")
            dp_area = area_layer.dataProvider()
            a_fields = QgsFields(); a_fields.append(QgsField("ID", QVariant.Int)); a_fields.append(QgsField("befahrbar", QVariant.Int))
            dp_area.addAttributes(a_fields); area_layer.updateFields()

            self._apply_predefined_style(field_layer)
            self._apply_predefined_style(line_layer)
            self._apply_predefined_style(point_layer)
            self._apply_predefined_style(area_layer)

            return {"Feldgrenzen": field_layer, "Fahrspuren": line_layer, "Punkthindernis": point_layer, "Flaechenhindernis": area_layer}

        def _persist_frm_layers(layers_dict, ctr_name: str, frm_name: str, frm_group: QgsLayerTreeGroup):
            if not out_dir:
                return layers_dict
            base = os.path.join(out_dir, _safe(ctr_name), _safe(frm_name))
            os.makedirs(base, exist_ok=True)
            new_layers = {}
            tr_ctx = project.transformContext()
            for key, mem_layer in layers_dict.items():
                gpkg_path = os.path.join(base, f"{_safe(key)}.gpkg")
                opts = QgsVectorFileWriter.SaveVectorOptions()
                opts.driverName = "GPKG"; opts.layerName = key
                try:
                    opts.attributesToExport = [f.name() for f in mem_layer.fields() if f.name().lower() != 'fid']
                except Exception:  # nosec B110
                    pass
                _ = QgsVectorFileWriter.writeAsVectorFormatV3(mem_layer, gpkg_path, tr_ctx, opts)
                uri = f"{gpkg_path}|layername={key}"
                file_layer = QgsVectorLayer(uri, mem_layer.name(), "ogr")
                if file_layer.isValid():
                    parent = project.layerTreeRoot().findLayer(mem_layer.id()).parent()
                    project.removeMapLayer(mem_layer.id())
                    project.addMapLayer(file_layer, False)
                    if parent and isinstance(parent, QgsLayerTreeGroup):
                        parent.addLayer(file_layer)
                    else:
                        project.layerTreeRoot().addLayer(file_layer)

                    self._apply_predefined_style(file_layer)
                    if key == "Feldgrenzen":
                        self._apply_feldgrenzen_color(file_layer, frm_group)
                    new_layers[key] = file_layer
                else:
                    new_layers[key] = mem_layer
            self._reorder_frm_group_layers(frm_group)        
            return new_layers

        def _ensure_frm(frm_id: str, ctr_name_hint: str = None):
            # --- Namen bestimmen (und normalisieren) ---
            if frm_id in (None, "", "__UNBENANNT_FRM__"):
                ctr_name = _norm_name(ctr_name_hint or "Unbenannter Kunde")
                frm_name = _norm_name("Unbenannter Betrieb")
            else:
                info = frm_map.get(frm_id, {"name": frm_id, "ctr": None})
                ctr_name = ctr_map.get(info.get("ctr"), info.get("ctr") or ctr_name_hint or "Unbenannter Kunde")
                frm_name = info.get("name") or frm_id
                ctr_name = _norm_name(ctr_name)
                frm_name = _norm_name(frm_name)

            # >>> Cache-Key nach Namen, nicht nach ID <<<
            key = (ctr_name, frm_name)

            # Wenn bereits existiert -> zusammenführen (gleiches Layer-Set wiederverwenden)
            if key in per_frm_layers:
                return per_frm_layers[key], per_frm_groups[key], key

            # sonst neu anlegen
            frm_group = _ensure_hierarchy(ctr_name, frm_name)
            layers = _create_frm_layers()
            for lyr in layers.values():
                project.addMapLayer(lyr, False)
                frm_group.addLayer(lyr)
            
            self._apply_feldgrenzen_color(layers["Feldgrenzen"], frm_group)
            self._reorder_frm_group_layers(frm_group)

            layers = _persist_frm_layers(layers, ctr_name, frm_name, frm_group)

            # Felder-Katalog (Felder.csv) für diesen Betrieb vorbereiten
            per_frm_felder_rows.setdefault(key, {})
            if out_dir:
                base = os.path.join(out_dir, _safe(ctr_name), _safe(frm_name))
                csv_path = _felder_csv_path_in_dir(base)
                if not os.path.exists(csv_path):
                    _write_felder_csv(csv_path, {})
                per_frm_felder_csv[key] = csv_path
                felder_layer = _load_felder_layer(csv_path)
                if felder_layer is not None:
                    project.addMapLayer(felder_layer, False)
                    frm_group.addLayer(felder_layer)
                    per_frm_felder_layer[key] = felder_layer
            else:
                # Memory-Import (kein Zielordner): geometrieloser Felder-Layer
                per_frm_felder_csv[key] = None
                mem_felder = QgsVectorLayer("None", FELDER_LAYER_NAME, "memory")
                dpf = mem_felder.dataProvider()
                dpf.addAttributes([QgsField("id", QVariant.Int), QgsField("Name", QVariant.String)])
                mem_felder.updateFields()
                project.addMapLayer(mem_felder, False)
                frm_group.addLayer(mem_felder)
                per_frm_felder_layer[key] = mem_felder

            self._reorder_frm_group_layers(frm_group)

            per_frm_layers[key] = layers
            per_frm_groups[key] = frm_group
            return layers, frm_group, key

        # PFDs
        for pfd in root.findall('.//PFD'):
            pfd_id = pfd.get("A") or pfd.get("PFDId") or "PFD0"
            pfd_name = pfd.get("C") or pfd.get("B") or ""
            pfd_area = pfd.get("D", "0")
            frm_ref = pfd.get("F") or pfd.get("FRMIdRef")
            try:
                pfd_digits = ''.join(ch for ch in str(pfd_id) if ch.isdigit())
                if len(pfd_digits) == 10:
                    numeric_id = int(pfd_digits[-6:])
                else:
                    numeric_id = int(pfd_digits or 0)
            except Exception:
                numeric_id = 0

            pfd_area = pfd.get("D")
            pln_tmp = pfd.find("PLN")

            if _is_nullish(pfd_area) or str(pfd_area).strip() in ("0", "0.0"):
                if pln_tmp is not None:
                    pln_area = pln_tmp.get("C", "0")
                    if not _is_nullish(pln_area):
                        pfd_area = pln_area

            try:
                area_val = float(pfd_area)
            except Exception:
                area_val = 0.0
            

            ctr_ref_from_pfd = pfd.get("E") or pfd.get("CTRIdRef")

            # Default-Hierarchie, wenn im ISOXML wirklich nichts referenziert wird
            if not ctr_ref_from_pfd and not frm_ref:
                ctr_name_hint = "Unbenannter Kunde"
                frm_ref = "__UNBENANNT_FRM__"   # interner Schlüssel, damit Layer gesammelt werden
            else:
                ctr_name_hint = ctr_map.get(ctr_ref_from_pfd, ctr_ref_from_pfd or "Unbenannter Kunde")

            frm_layers, _grp, _frm_key = _ensure_frm(frm_ref or "__UNBENANNT_FRM__", ctr_name_hint)

            # jedes Feld (PFD) im Katalog registrieren – auch ohne Feldgrenze.
            # Vorhandenen, nicht-leeren Namen nicht durch einen leeren überschreiben.
            _cur_rows = per_frm_felder_rows.setdefault(_frm_key, {})
            _new_name = pfd_name or ""
            if numeric_id not in _cur_rows or (not _cur_rows.get(numeric_id) and _new_name):
                _cur_rows[numeric_id] = _new_name

            field_layer = frm_layers["Feldgrenzen"]; line_layer = frm_layers["Fahrspuren"]
            point_layer = frm_layers["Punkthindernis"]; area_layer = frm_layers["Flaechenhindernis"]
            dp_field = field_layer.dataProvider(); dp_line = line_layer.dataProvider()
            dp_point = point_layer.dataProvider(); dp_area = area_layer.dataProvider()

            # Boundary - explizit PolygonType "1" (Partfield Boundary), sonst koennte bei
            # Feldern ohne Grenze faelschlich eine Hindernis-PLN (Typ 6/8) als Grenze gelesen werden.
            pln = pfd.find("PLN[@A='1']")   # erste Grenz-PLN (fuer Legacy-Hindernis-Block unten)

            # Boundary - ALLE PLN mit PolygonType "1" (Partfield Boundary) einlesen.
            # Mehrere PLN A=1 (bzw. mehrere LSG A=1) pro PFD = Multipolygon-Grenze:
            # je Polygon eine eigene Feldgrenzen-Feature mit derselben Feld-ID.
            # Das entspricht dem Export (eine PLN je Feldgrenze) -> sauberer Roundtrip.
            boundary_feats = []
            for pln_b in pfd.findall("PLN[@A='1']"):
                for lsg_field in pln_b.findall("LSG[@A='1']"):
                    ring_pts = []
                    ring_pts_wgs84 = []

                    for pnt in lsg_field.findall("PNT"):
                        a_val = pnt.get("A")
                        if a_val in ("10", "2"):
                            lat = float(pnt.get("C", "0"))
                            lon = float(pnt.get("D", "0"))

                            ring_pts_wgs84.append(QgsPointXY(lon, lat))
                            ring_pts.append(_tx_pt_xy(lon, lat))

                    if len(ring_pts) > 2:
                        # Fläche IMMER aus der Geometrie berechnen (nie aus der Datei).
                        final_area_val = _calc_area_from_ring_wgs84(ring_pts_wgs84)

                        # Grenzenname aus PLN@B (PolygonDesignator). Ist er leer oder
                        # nicht vorhanden, heißt die Feldgrenze wie das Feld (PFD@C).
                        bname = pln_b.get("B")
                        if _is_nullish(bname) or not str(bname).strip():
                            bname = pfd_name

                        feat_f = QgsFeature(field_layer.fields())
                        feat_f.setAttribute("ID", numeric_id)
                        feat_f.setAttribute("Name", bname)
                        feat_f.setAttribute("Flaeche", final_area_val)
                        feat_f.setGeometry(QgsGeometry.fromPolygonXY([ring_pts]))
                        boundary_feats.append(feat_f)

            if boundary_feats:
                dp_field.addFeatures(boundary_feats)

            # Area obstacles - neues, normgerechtes Format: eigene PLN mit
            # PolygonType "6" (Obstacle, nicht befahrbar) oder "8" (Other, befahrbar).
            for hind_pln in pfd.findall("PLN"):
                hind_type = hind_pln.get("A")
                if hind_type not in ("6", "8"):
                    continue
                bf_val = 1 if hind_type == "8" else 0
                for lsg_hind in hind_pln.findall("LSG"):
                    ring2 = []
                    for pnt2 in lsg_hind.findall("PNT"):
                        if pnt2.get("A") in ("10", "2"):
                            lat2 = float(pnt2.get("C", "0")); lon2 = float(pnt2.get("D", "0"))
                            ring2.append(_tx_pt_xy(lon2, lat2))
                    if len(ring2) > 2:
                        feat_a = QgsFeature(area_layer.fields())
                        feat_a.setAttribute("ID", numeric_id); feat_a.setAttribute("befahrbar", bf_val)
                        feat_a.setGeometry(QgsGeometry.fromPolygonXY([ring2]))
                        dp_area.addFeatures([feat_a])

            # Area obstacles - Legacy-Format aus aelteren Exporten (< Fix vom Juli 2026):
            # P094_Impassable war ein nicht-normkonformes Custom-Attribut auf einer LSG,
            # verschachtelt in der ersten PLN. Bleibt fuer den Import alter Dateien erhalten.
            if pln is not None:
                for lsg_area in pln.findall("LSG"):
                    if lsg_area.get("A") == "2" and lsg_area.get("P094_Impassable") is not None:
                        impass = lsg_area.get("P094_Impassable", "0")
                        bf_val = 1 if impass == "0" else 0
                        ring2 = []
                        for pnt2 in lsg_area.findall("PNT"):
                            if pnt2.get("A") in ("10", "2"):
                                lat2 = float(pnt2.get("C", "0")); lon2 = float(pnt2.get("D", "0"))
                                ring2.append(_tx_pt_xy(lon2, lat2))
                        if len(ring2) > 2:
                            feat_a = QgsFeature(area_layer.fields())
                            feat_a.setAttribute("ID", numeric_id); feat_a.setAttribute("befahrbar", bf_val)
                            feat_a.setGeometry(QgsGeometry.fromPolygonXY([ring2]))
                            dp_area.addFeatures([feat_a])



            # Point obstacles
            for pnt_h in pfd.findall("PNT"):
                a_attr = pnt_h.get("A", "")
                if a_attr in ["1", "2", "5"]:
                    lat = float(pnt_h.get("C", "0")); lon = float(pnt_h.get("D", "0"))
                    hind_name = pnt_h.get("B", "")
                    if is_v3:
                        # v3 kennt kein PointType "Obstacle" (5, erst ab v4) - beim Export wird
                        # ein nicht befahrbares Hindernis deshalb als "2"=other geschrieben
                        # (siehe Export-Fix). Beim Import muss das spiegelbildlich wieder als
                        # nicht befahrbar erkannt werden, sonst geht die Information verloren.
                        bf_val = 1 if a_attr == "1" else 0
                    else:
                        bf_val = 0 if a_attr == "5" else 1
                    feat_pt = QgsFeature(point_layer.fields())
                    feat_pt.setAttribute("ID", numeric_id); feat_pt.setAttribute("Name", hind_name); feat_pt.setAttribute("befahrbar", bf_val)
                    feat_pt.setGeometry(QgsGeometry.fromPointXY(_tx_pt_xy(lon, lat)))
                    dp_point.addFeatures([feat_pt])

            # Swaths
            # Beide Repräsentationen unabhängig von VersionMajor prüfen: manche
            # Terminals (z.B. CNH/Case) schreiben Spuren auch in "v3"-Dateien
            # als echte ISO-Guidance-Patterns (GGP/GPN) statt als flaches
            # <LSG A="5"> direkt in <PFD>. pfd.findall("LSG") findet nur
            # DIREKTE Kinder von PFD, also keine Überschneidung/Doppelzählung
            # mit den in GGP/GPN verschachtelten LSG-Elementen weiter unten.
            for lsg_line in pfd.findall("LSG"):
                if lsg_line.get("A") == "5":
                    track_name = lsg_line.get("B", "")
                    line_pts = []
                    for pnt_spur in lsg_line.findall("PNT"):
                        lat = float(pnt_spur.get("C", "0")); lon = float(pnt_spur.get("D", "0"))
                        line_pts.append(_tx_pt_xy(lon, lat))
                    if len(line_pts) >= 2:
                        feat_line = QgsFeature(line_layer.fields())
                        feat_line.setAttribute("ID", numeric_id); feat_line.setAttribute("Name", track_name)
                        feat_line.setGeometry(QgsGeometry.fromPolylineXY(line_pts))
                        dp_line.addFeatures([feat_line])

            for ggp in pfd.findall("GGP"):
                gpn_all = [gpn for gpn in ggp.findall("GPN")]
                gpn_tracks = []
                for gpn in gpn_all:
                    lsg_track = gpn.find("LSG[@A='5']")
                    if lsg_track is not None:
                        gpn_tracks.append((gpn, lsg_track))
                multi = len(gpn_tracks) > 1
                ggp_B = ggp.get("B")
                seg_label = ggp_B.strip() if (multi and not _is_nullish(ggp_B)) else None
                for gpn, lsg_track in gpn_tracks:
                    gpn_B = gpn.get("B")
                    if multi:
                        track_name = (gpn_B or '').strip()
                    else:
                        track_name = gpn_B.strip() if not _is_nullish(gpn_B) else (ggp_B or '').strip()
                    line_pts = []
                    for pnt_spur in lsg_track.findall("PNT"):
                        lat = float(pnt_spur.get("C", "0")); lon = float(pnt_spur.get("D", "0"))
                        line_pts.append(_tx_pt_xy(lon, lat))
                    if len(line_pts) >= 2:
                        feat_line = QgsFeature(line_layer.fields())
                        feat_line.setAttribute("ID", numeric_id); feat_line.setAttribute("Name", track_name)
                        if seg_label is not None:
                            feat_line.setAttribute("Segment", seg_label)
                        feat_line.setGeometry(QgsGeometry.fromPolylineXY(line_pts))
                        dp_line.addFeatures([feat_line])

        for layers in per_frm_layers.values():
            for lyr in layers.values():
                lyr.updateExtents()

        # Felder-Katalog je Betrieb schreiben / füllen
        for key, rows in per_frm_felder_rows.items():
            csv_path = per_frm_felder_csv.get(key)
            felder_layer = per_frm_felder_layer.get(key)
            frm_group = per_frm_groups.get(key)
            if csv_path:
                # bestehende Einträge (z.B. manuell) erhalten, neue ergänzen
                merged = _read_felder_csv(csv_path)
                for fid, nm in rows.items():
                    if fid not in merged or (not merged.get(fid) and nm):
                        merged[fid] = nm
                _write_felder_csv(csv_path, merged)
                # Felder-Layer FRISCH neu laden (statt nur reload). Der Layer
                # wurde anfangs aus einer leeren CSV erzeugt; ein bloßes reload
                # füllt die Value-Relation-Quelle nicht zuverlässig, daher
                # entfernen + neu laden -> Dropdown zeigt sofort die Namen.
                if frm_group is not None:
                    self._recreate_felder_layer(frm_group, csv_path)
            elif isinstance(felder_layer, QgsVectorLayer):
                # Memory-Variante: Features direkt einfügen
                feats = []
                for fid in sorted(rows.keys()):
                    f = QgsFeature(felder_layer.fields())
                    f.setAttribute("id", int(fid))
                    f.setAttribute("Name", rows.get(fid, ""))
                    feats.append(f)
                if feats:
                    felder_layer.dataProvider().addFeatures(feats)
                    felder_layer.updateExtents()

        # Feld-Dropdown (Value Relation) direkt nach dem Import setzen,
        # damit die Attributtabelle sofort den Feldnamen/Dropdown zeigt und
        # nicht erst beim nächsten Öffnen des Path Planners.
        self._apply_language_to_project()
        self._apply_field_dropdowns()

        self.iface.messageBar().pushMessage(
            "Success", _tr("ISOXML importiert (CTR → FRM → Layer)."), level=Qgis.Success, duration=4)
        self.dlg.accept()

    # ------------------------- EXPORT -------------------------
    def _do_export(self):
        def _find_required_memory_layers():
            """
            Prüft nur exportrelevante Layer in allen Kunden-/Betriebsgruppen.
            Andere temporäre Layer im Projekt sind erlaubt.
            """
            required_names = {
                "Feldgrenzen",
                "Fahrspuren",
                "Flaechenhindernis",
                "Punkthindernis",
            }

            problems = []
            root = QgsProject.instance().layerTreeRoot()

            for ctr_node in root.children():
                if not isinstance(ctr_node, QgsLayerTreeGroup):
                    continue

                ctr_name = ctr_node.name()

                for frm_node in ctr_node.children():
                    if not isinstance(frm_node, QgsLayerTreeGroup):
                        continue

                    frm_name = frm_node.name()

                    for child in frm_node.children():
                        try:
                            lyr = child.layer()
                        except Exception:
                            lyr = None

                        if not isinstance(lyr, QgsVectorLayer):
                            continue

                        if translations.canonical_layer_name(lyr.name()) not in required_names:
                            continue

                        if lyr.providerType() == "memory":
                            problems.append(f"{ctr_name} / {frm_name} / {_display_layer_name(lyr.name())}")

            return problems

        memory_problems = _find_required_memory_layers()
        if memory_problems:
            preview = "\n".join(memory_problems[:8])
            if len(memory_problems) > 8:
                preview += _tr("\n… und {n} weitere").format(n=len(memory_problems) - 8)

            self.iface.messageBar().pushMessage(
                _tr("Export nicht möglich"),
                _tr("Folgende exportrelevante Layer sind noch temporär:\n"
                    "{preview}\n\n"
                    "Bitte diese Layer zuerst dauerhaft speichern.").format(preview=preview),
                level=Qgis.Warning,
                duration=10
            )
            return


        out_dir = self.dlg.out_line.text().strip()

        # Exportformat aus dem gewählten Terminal ableiten
        term_brand, term_model, term_fmt = self.dlg.selected_terminal()
        if term_fmt is None:
            self.iface.messageBar().pushMessage(
                _tr("Fehler"), _tr("Bitte ein Terminal auswählen."), level=Qgis.Warning, duration=4
            )
            return
        is_aggps = (term_fmt == "AgGPS")
        is_john_deere = (term_fmt == "Gen4")
        is_v3 = (term_fmt == "3.3")
        # Kontursegmente nur für Fendt One
        use_segments = (self.dlg.chk_seg.isChecked() and _is_fendt_one(term_brand, term_model))
        densify_curves = self.dlg.chk_densify_curves.isChecked()
        densify_interval_m = float(self.dlg.spin_densify_interval.value())
        extend_curves = self.dlg.chk_extend_curves.isChecked()
        extend_curves_m = float(self.dlg.spin_extend_curves.value())

        if not out_dir:
            self.iface.messageBar().pushMessage(
                _tr("Fehler"), _tr("Bitte Zielordner wählen."),
                level=Qgis.Warning, duration=4
            )
            return

        selected = self.dlg.selected_export_map()
        if not selected:
            self.iface.messageBar().pushMessage(
                _tr("Hinweis"), _tr("Keine Auswahl getroffen."),
                level=Qgis.Info, duration=4
            )
            return

        if is_aggps:
            try:
                ok = export_aggps(self, out_dir, selected)
                if ok:
                    self.iface.messageBar().pushMessage(
                        _tr("Erfolgreich"),
                        _tr("AgGPS-Export erstellt: {path}").format(
                            path=os.path.join(out_dir, 'AgGPS', 'Data')),
                        level=Qgis.Success,
                        duration=4
                    )
                    self.dlg.accept()
                else:
                    self.iface.messageBar().pushMessage(
                        _tr("Hinweis"),
                        _tr("Für die Auswahl gab es keine exportierbaren Daten."),
                        level=Qgis.Info, duration=5
                    )
                return
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    _tr("Fehler"),
                    _tr("AgGPS-Export fehlgeschlagen: {e}").format(e=e),
                    level=Qgis.Critical,
                    duration=6
                )
                return

        if is_john_deere:
            try:
                ok = export_john_deere_gen4(self, out_dir, selected)
                if ok:
                    self.iface.messageBar().pushMessage(
                        _tr("Erfolgreich"),
                        _tr("John Deere Gen4 Export erstellt: {out_dir}").format(out_dir=out_dir),
                        level=Qgis.Success,
                        duration=4
                    )
                    self.dlg.accept()
                return
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    _tr("Fehler"),
                    _tr("John Deere Gen4 Export fehlgeschlagen: {e}").format(e=e),
                    level=Qgis.Critical,
                    duration=6
                )
                return
        
        if not os.path.exists(out_dir + "/TASKDATA"):
            os.makedirs(out_dir + "/TASKDATA")

        out_dir_taskdata = out_dir + "/TASKDATA"


        output_file_path = os.path.join(out_dir_taskdata, "TASKDATA.XML")

        root_xml = ET.Element('ISO11783_TaskData', {
            "VersionMajor": "3" if is_v3 else "4",
            # v3.3-Schema: VersionMinor ist "fixed" auf "3" (ISO11783_TaskFile_V3-3.xsd).
            # v4.3-Schema erlaubt 0-3; wir schreiben ebenfalls "3" fuer volle 4.3-Konformitaet.
            "VersionMinor": "3",
            "ManagementSoftwareManufacturer": "LK-Technik Mold",
            "ManagementSoftwareVersion": "2.1.1",
            "DataTransferOrigin": "1"
        })

        ctr_idx = 1
        frm_idx = 1
        pnt_global = 1
        pln_global = 1
        ggp_global = 1
        gpn_global = 1

        CTR_WIDTH = 2
        FRM_WIDTH = 2
        FIELD_WIDTH = 6

        project = QgsProject.instance()

        def _make_pfd_id(ctr_num: int, frm_num: int, field_id: int) -> str:
            return f"PFD{ctr_num:0{CTR_WIDTH}d}{frm_num:0{FRM_WIDTH}d}{field_id:0{FIELD_WIDTH}d}"

        def _fmt_coord(value) -> str:
            """PointNorth/PointEast (PNT@C/@D) normgerecht formatieren.
            ISO11783_Common_V4-3.xsd begrenzt beide Attribute auf max. 9 Nachkommastellen
            (xs:fractionDigits=9). In v3 gibt es diese Grenze zwar nicht, aber 9 Nachkommastellen
            entsprechen ohnehin < 0.1 mm Genauigkeit, daher einheitlich fuer v3 und v4 gerundet."""
            return f"{float(value):.9f}"
        
        def next_ggp_id():
            nonlocal ggp_global
            gid = f"GGP{ggp_global:04d}"
            ggp_global += 1
            return gid

        def next_gpn_id():
            nonlocal gpn_global
            gid = f"GPN{gpn_global:04d}"
            gpn_global += 1
            return gid


        def _iter_ctr_groups():
            root = project.layerTreeRoot()
            for node in root.children():
                if isinstance(node, QgsLayerTreeGroup):
                    yield node

        def _iter_frm_groups(ctr_group: QgsLayerTreeGroup):
            for node in ctr_group.children():
                if isinstance(node, QgsLayerTreeGroup):
                    yield node

        def _find_child_layer_by_name(group: QgsLayerTreeGroup, name: str) -> QgsVectorLayer:
            return _find_child_layer(group, name)

        exported_any = False

        for ctr_group in _iter_ctr_groups():
            ctr_name = ctr_group.name()
            if ctr_name not in selected:
                continue

            ctr_num = ctr_idx
            ctr_id = f"CTR{ctr_idx}"
            ctr_idx += 1

            ET.SubElement(root_xml, 'CTR', {'A': ctr_id, 'B': ctr_name})

            frm_num_within_ctr = 1

            for frm_group in _iter_frm_groups(ctr_group):
                frm_name = frm_group.name()
                if frm_name not in selected[ctr_name]:
                    continue

                frm_num = frm_num_within_ctr
                frm_num_within_ctr += 1

                frm_id = f"FRM{frm_idx}"
                frm_idx += 1

                ET.SubElement(root_xml, 'FRM', {'A': frm_id, 'B': frm_name, 'I': ctr_id})

                polygon_layer = _find_child_layer_by_name(frm_group, "Feldgrenzen")
                line_layer = _find_child_layer_by_name(frm_group, "Fahrspuren")
                point_layer = _find_child_layer_by_name(frm_group, "Punkthindernis")
                fh_layer = _find_child_layer_by_name(frm_group, "Flaechenhindernis")

                if not polygon_layer:
                    # ohne Feldgrenzen-Layer nur überspringen, wenn auch
                    # kein Feld-Katalog (Felder.csv) vorhanden ist.
                    if not _field_catalog_for_frm(frm_group):
                        continue

                #Transform to WGS84 for Export
                wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
                project_ctx = QgsProject.instance()

                def _maybe_ct(layer):
                    if layer and layer.isValid() and layer.crs().isValid() and layer.crs() != wgs84:
                        return QgsCoordinateTransform(layer.crs(), wgs84, project_ctx)
                    return None

                ct_poly  = _maybe_ct(polygon_layer)
                ct_line  = _maybe_ct(line_layer)
                ct_point = _maybe_ct(point_layer)
                ct_area  = _maybe_ct(fh_layer)

                def _to_wgs_xy_from_point(pt, ct):
                    x = pt.x(); y = pt.y()
                    if ct:
                        pxy = ct.transform(QgsPointXY(x, y))
                        return pxy.x(), pxy.y()  # lon, lat
                    return x, y
                
                def _metric_crs_for_layer(layer):
                    """
                    Liefert ein metrisches CRS für die Verdichtung.
                    Priorität:
                    1) Layer-CRS, wenn metrisch
                    2) Projekt-CRS, wenn metrisch
                    3) fallback: EPSG:32633
                    """
                    try:
                        if layer and layer.crs().isValid() and layer.crs().mapUnits() == Qgis.DistanceUnit.Meters:
                            return layer.crs()
                    except Exception:  # nosec B110
                        pass

                    try:
                        prj_crs = QgsProject.instance().crs()
                        if prj_crs.isValid() and prj_crs.mapUnits() == Qgis.DistanceUnit.Meters:
                            return prj_crs
                    except Exception:  # nosec B110
                        pass

                    return QgsCoordinateReferenceSystem("EPSG:32633")


                def _densify_geometry_for_export(geom, source_layer, interval_m):
                    """
                    Verdichtet eine Geometrie NUR auf einer Kopie.
                    Rückgabe in WGS84-Geometrie, damit der restliche Export unverändert bleibt.
                    """
                    if geom is None or geom.isEmpty():
                        return None

                    try:
                        geom_copy = QgsGeometry(geom)
                    except Exception:
                        geom_copy = geom.constGet().clone()
                        geom_copy = QgsGeometry(geom_copy)

                    metric_crs = _metric_crs_for_layer(source_layer)
                    source_crs = source_layer.crs()
                    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
                    project_ctx = QgsProject.instance()

                    to_metric = None
                    to_wgs = None

                    try:
                        if source_crs.isValid() and source_crs != metric_crs:
                            to_metric = QgsCoordinateTransform(source_crs, metric_crs, project_ctx)
                    except Exception:
                        to_metric = None

                    try:
                        if metric_crs.isValid() and metric_crs != wgs84:
                            to_wgs = QgsCoordinateTransform(metric_crs, wgs84, project_ctx)
                    except Exception:
                        to_wgs = None

                    if to_metric:
                        try:
                            geom_copy.transform(to_metric)
                        except Exception:
                            return None

                    try:
                        geom_copy = geom_copy.densifyByDistance(interval_m)
                    except Exception:
                        return None

                    if to_wgs:
                        try:
                            geom_copy.transform(to_wgs)
                        except Exception:
                            return None
                    elif source_crs.isValid() and source_crs != wgs84:
                        try:
                            direct_to_wgs = QgsCoordinateTransform(source_crs, wgs84, project_ctx)
                            geom_copy.transform(direct_to_wgs)
                        except Exception:
                            return None

                    return geom_copy
                
                def _extend_line_both_ends(line_pts, extend_m):
                    """
                    Verlängert eine einzelne Linie an Anfang und Ende um extend_m Meter.
                    Erwartet Punkte in einem metrischen CRS.
                    Gibt eine neue Punktliste zurück.
                    """
                    if not line_pts or len(line_pts) < 2 or extend_m <= 0:
                        return line_pts

                    new_line = list(line_pts)

                    # Anfang verlängern: Richtung aus erstem Segment ableiten
                    p0 = new_line[0]
                    p1 = new_line[1]
                    dx0 = p1.x() - p0.x()
                    dy0 = p1.y() - p0.y()
                    len0 = math.hypot(dx0, dy0)

                    if len0 > 0:
                        ux0 = dx0 / len0
                        uy0 = dy0 / len0
                        new_start = QgsPointXY(
                            p0.x() - ux0 * extend_m,
                            p0.y() - uy0 * extend_m
                        )
                        new_line[0] = new_start

                    # Ende verlängern: Richtung aus letztem Segment ableiten
                    pn1 = new_line[-2]
                    pn = new_line[-1]
                    dx1 = pn.x() - pn1.x()
                    dy1 = pn.y() - pn1.y()
                    len1 = math.hypot(dx1, dy1)

                    if len1 > 0:
                        ux1 = dx1 / len1
                        uy1 = dy1 / len1
                        new_end = QgsPointXY(
                            pn.x() + ux1 * extend_m,
                            pn.y() + uy1 * extend_m
                        )
                        new_line[-1] = new_end

                    return new_line
                
                def _extend_geometry_for_export(geom, source_layer, extend_m):
                    """
                    Verlängert Liniengeometrien an Anfang und Ende um extend_m Meter.
                    Arbeitet nur auf einer Kopie und gibt WGS84-Geometrie zurück.
                    """
                    if geom is None or geom.isEmpty() or extend_m <= 0:
                        return None

                    lines_src = _geometry_to_lines_xy(geom)
                    if not lines_src:
                        return None

                    metric_crs = _metric_crs_for_layer(source_layer)
                    source_crs = source_layer.crs()
                    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
                    project_ctx = QgsProject.instance()

                    to_metric = None
                    to_wgs = None

                    try:
                        if source_crs.isValid() and source_crs != metric_crs:
                            to_metric = QgsCoordinateTransform(source_crs, metric_crs, project_ctx)
                    except Exception:
                        to_metric = None

                    try:
                        if metric_crs.isValid() and metric_crs != wgs84:
                            to_wgs = QgsCoordinateTransform(metric_crs, wgs84, project_ctx)
                    except Exception:
                        to_wgs = None

                    metric_lines = []
                    for line in lines_src:
                        metric_line = []
                        for pt in line:
                            p = QgsPointXY(pt.x(), pt.y())
                            if to_metric:
                                try:
                                    p = to_metric.transform(p)
                                except Exception:
                                    return None
                            metric_line.append(p)
                        metric_lines.append(metric_line)

                    extended_metric_lines = []
                    for line in metric_lines:
                        # nur Kurven mit mehr als 2 Stützpunkten verlängern
                        if len(line) > 2:
                            ext_line = _extend_line_both_ends(line, extend_m)
                        else:
                            ext_line = line
                        extended_metric_lines.append(ext_line)

                    wgs_lines = []
                    for line in extended_metric_lines:
                        wgs_line = []
                        for pt in line:
                            p = QgsPointXY(pt.x(), pt.y())
                            if to_wgs:
                                try:
                                    p = to_wgs.transform(p)
                                except Exception:
                                    return None
                            elif source_crs.isValid() and source_crs != wgs84 and not to_metric:
                                try:
                                    direct_to_wgs = QgsCoordinateTransform(source_crs, wgs84, project_ctx)
                                    p = direct_to_wgs.transform(p)
                                except Exception:
                                    return None
                            wgs_line.append(p)
                        wgs_lines.append(wgs_line)

                    try:
                        if len(wgs_lines) == 1:
                            return QgsGeometry.fromPolylineXY(wgs_lines[0])
                        return QgsGeometry.fromMultiPolylineXY(wgs_lines)
                    except Exception:
                        return None
                
                def _geometry_to_lines_xy(geom):
                    """
                    Wandelt eine Linien-Geometrie robust in eine Liste von Linien um.
                    Rückgabeformat:
                        [
                            [QgsPointXY, QgsPointXY, ...],   # eine Linie
                            [QgsPointXY, QgsPointXY, ...],   # weitere Linie
                        ]
                    Funktioniert für:
                    - LineString
                    - MultiLineString
                    """
                    if geom is None or geom.isEmpty():
                        return []

                    # zuerst versuchen: MultiLine
                    try:
                        lines = geom.asMultiPolyline()
                        if lines:
                            return lines
                    except Exception:  # nosec B110
                        pass

                    # dann versuchen: einzelne Line
                    try:
                        line = geom.asPolyline()
                        if line:
                            return [line]
                    except Exception:  # nosec B110
                        pass

                    return []

                def _export_lines_from_feature(
                    track_feature,
                    line_layer,
                    densify_enabled=False,
                    interval_m=1.0,
                    extend_enabled=False,
                    extend_m=0.0
                ):
                    """
                    Gibt exportierbare Linien als Liste von Polylinien in WGS84 zurück.
                    Optional:
                    - Verdichtung nur bei Kurven (>2 Punkte)
                    - Verlängerung an Anfang und Ende nur bei Kurven (>2 Punkte)
                    """
                    geom = track_feature.geometry()
                    if geom is None or geom.isEmpty():
                        return []

                    raw_lines = _geometry_to_lines_xy(geom)
                    if not raw_lines:
                        return []

                    has_curve = any(len(line) > 2 for line in raw_lines)

                    working_geom = geom
                    working_layer = line_layer

                    # 1) optional verdichten
                    if densify_enabled and has_curve:
                        densified_geom = _densify_geometry_for_export(working_geom, working_layer, interval_m)
                        if densified_geom is not None and not densified_geom.isEmpty():
                            working_geom = densified_geom

                    # 2) optional verlängern
                    if extend_enabled and has_curve:
                        extended_geom = _extend_geometry_for_export(working_geom, working_layer, extend_m)
                        if extended_geom is not None and not extended_geom.isEmpty():
                            working_geom = extended_geom

                    # working_geom liegt nach den Export-Hilfsfunktionen in WGS84,
                    # wenn eine Bearbeitung aktiv war. Sonst noch nach WGS84 transformieren.
                    edited = (
                        (densify_enabled and has_curve)
                        or (extend_enabled and has_curve)
                    )

                    if edited:
                        final_lines = _geometry_to_lines_xy(working_geom)
                        return final_lines

                    # Standardweg ohne Bearbeitung: Original nach WGS84 transformieren
                    result = []
                    for line in raw_lines:
                        wgs_line = []
                        for pt in line:
                            lon, lat = _to_wgs_xy_from_point(pt, ct_line)
                            wgs_line.append(QgsPointXY(lon, lat))
                        if wgs_line:
                            result.append(wgs_line)
                    return result

                field_ids_filter = selected[ctr_name][frm_name]

                poly_fmap = _field_map(polygon_layer) if polygon_layer else {}
                id_field   = _pick_field(poly_fmap, "ID")
                name_field = _pick_field(poly_fmap, "Name")
                area_field = _pick_field(poly_fmap, "Flaeche")

                # Export läuft über den Feld-Katalog (Felder.csv) statt über
                # die Feldgrenzen. Dadurch werden auch Felder OHNE Feldgrenze
                # exportiert; mehrere Feldgrenzen pro Feld sind möglich.
                catalog = _field_catalog_for_frm(frm_group)
                if not catalog:
                    continue

                # Feldgrenzen je Feld-ID gruppieren (0..n)
                boundary_by_id = {}
                if polygon_layer is not None:
                    for _bf in polygon_layer.getFeatures():
                        try:
                            _bid = int(_bf[id_field]) if id_field else int(_bf.id())
                        except Exception:  # nosec B112
                            continue
                        boundary_by_id.setdefault(_bid, []).append(_bf)

                for field_id, cat_name in catalog:
                    if (field_ids_filter is not None) and (field_id not in field_ids_filter):
                        continue

                    boundaries = boundary_by_id.get(field_id, [])
                    # erste Feldgrenze (für Name/Fläche des Feldes); None = keine Grenze
                    field_feature = boundaries[0] if boundaries else None

                    # PFD-Name = Feld-(Katalog-)Name. Fläche aus der ersten Grenze.
                    # Die Namen einzelner Grenzen fließen NICHT in den Feldnamen,
                    # sondern in die jeweilige PLN (siehe unten).
                    field_name = cat_name or str(field_id)
                    field_area = 0
                    if field_feature is not None and area_field:
                        try:
                            _ba = field_feature[area_field]
                            field_area = float(_ba) if not _is_nullish(_ba) else 0
                        except Exception:
                            field_area = 0

                    pfd_unique_id = _make_pfd_id(ctr_num, frm_num, field_id)

                    pfd_element = ET.SubElement(root_xml, 'PFD', {
                        'A': pfd_unique_id,
                        'C': str(field_name),
                        'D': str(int(field_area)),
                        'E': ctr_id,
                        'F': frm_id
                    })

                    #Boundary – je Feldgrenze EINE eigene PLN mit EIGENEM Namen (B).
                    # Mehrere Grenzen pro Feld können so unterschiedlich heißen.
                    # Felder ohne Grenze erhalten ein PFD ohne PLN.
                    pln_element = None
                    for bf in boundaries:
                        bname = field_name
                        if name_field:
                            _bn = bf[name_field]
                            if not _is_nullish(_bn):
                                bname = str(_bn)
                        barea = field_area
                        if area_field:
                            try:
                                _bv = bf[area_field]
                                barea = float(_bv) if not _is_nullish(_bv) else field_area
                            except Exception:
                                barea = field_area

                        pln_attrs = {'A': '1', 'B': str(bname), 'C': str(int(barea))}
                        if not is_v3:
                            # PolygonId (E) gibt es erst ab v4 und muss dokumentweit eindeutig sein
                            # (xs:ID) - daher ein global fortlaufender Zaehler statt field_id, das bei
                            # mehreren Grenzen pro Feld sonst mehrfach vergeben wuerde.
                            pln_attrs['E'] = f'PLN{pln_global}'
                            pln_global += 1
                        this_pln = ET.SubElement(pfd_element, 'PLN', pln_attrs)
                        if pln_element is None:
                            pln_element = this_pln

                        lsg_field = ET.SubElement(this_pln, 'LSG', {'A': '1'})
                        geom = bf.geometry()
                        polys = geom.asMultiPolygon() or []
                        if not polys:
                            single_poly = geom.asPolygon()
                            if single_poly:
                                polys = [single_poly]
                        for polygon in polys:
                            for ring in polygon:
                                for pt in ring:
                                    lon, lat = _to_wgs_xy_from_point(pt, ct_poly)
                                    ET.SubElement(lsg_field, 'PNT', {'A': '2', 'C': _fmt_coord(lat), 'D': _fmt_coord(lon)})

                    #Area obstacles
                    if fh_layer is not None:
                        fh_names = fh_layer.fields().names()
                        for fh_feature in fh_layer.getFeatures():
                            fh_fmap = _field_map(fh_layer)
                            raw = _feat_val(fh_feature, fh_fmap, "ID", "field_id", default=None)
                            if raw is None:
                                continue
                            try:
                                if int(raw) != int(field_id):
                                    continue
                            except Exception:  # nosec B112
                                continue
                            bf_val = fh_feature['befahrbar'] if 'befahrbar' in fh_names else 0
                            # Normgerecht: statt eines Custom-Attributs (P094_Impassable, das es in
                            # keiner ISOXML-Version gibt) bekommt jedes Flaechenhindernis eine eigene
                            # PLN mit offiziellem PolygonType: "6"=Obstacle (nicht befahrbar) bzw.
                            # "8"=Other (befahrbar, aber markierte Flaeche).
                            hind_poly_type = '6' if bf_val == 0 else '8'
                            hind_pln_attrs = {'A': hind_poly_type, 'C': str(0)}
                            if not is_v3:
                                hind_pln_attrs['E'] = f'PLN{pln_global}'
                                pln_global += 1
                            hind_pln = ET.SubElement(pfd_element, 'PLN', hind_pln_attrs)
                            lsg_hind = ET.SubElement(hind_pln, 'LSG', {'A': '1'})
                            fh_geom = fh_feature.geometry()
                            polys2 = fh_geom.asMultiPolygon() or []
                            if not polys2:
                                single2 = fh_geom.asPolygon()
                                if single2:
                                    polys2 = [single2]
                            for poly2 in polys2:
                                for ring2 in poly2:
                                    for pt2 in ring2:
                                        lon2, lat2 = _to_wgs_xy_from_point(pt2, ct_area)
                                        ET.SubElement(lsg_hind, 'PNT', {'A': '2', 'C': _fmt_coord(lat2), 'D': _fmt_coord(lon2)})

                    #Point obstacles
                    if point_layer is not None:
                        p_names = point_layer.fields().names()
                        for hindernis in point_layer.getFeatures():
                            p_fmap = _field_map(point_layer)
                            raw = _feat_val(hindernis, p_fmap, "ID", "field_id", default=None)
                            if raw is None:
                                continue
                            try:
                                if int(raw) != int(field_id):
                                    continue
                            except Exception:  # nosec B112
                                continue
                            bf_val = hindernis['befahrbar'] if 'befahrbar' in p_names else 1
                            if is_v3:
                                # v3 PointType kennt nur 1=Flag und 2=other, kein Obstacle-Typ (5).
                                a_val = "1" if bf_val == 1 else "2"
                            else:
                                a_val = "1" if bf_val == 1 else "5"
                            hind_name = hindernis['name'] if 'name' in p_names else (hindernis['Name'] if 'Name' in p_names else '')
                            pt_geom = hindernis.geometry().asPoint()
                            lonp, latp = _to_wgs_xy_from_point(pt_geom, ct_point)
                            pnt_attrs = {'A': a_val, 'B': hind_name, 'C': _fmt_coord(latp), 'D': _fmt_coord(lonp)}
                            if not is_v3:
                                # PointId (G) gibt es erst ab v4
                                pnt_attrs['G'] = f"PNT{pnt_global}"
                                pnt_global += 1
                            ET.SubElement(pfd_element, 'PNT', pnt_attrs)

                    #Swaths
                    if line_layer is not None:
                        line_names = line_layer.fields().names()
                        if is_v3:
                            for track_feature in line_layer.getFeatures():
                                line_fmap = _field_map(line_layer)
                                raw = _feat_val(track_feature, line_fmap, "ID", "field_id", default=None)
                                if raw is None:
                                    continue
                                try:
                                    if int(raw) != int(field_id):
                                        continue
                                except Exception:  # nosec B112
                                    continue

                                track_name = track_feature['Name'] if 'Name' in line_names else ''

                                lines = _export_lines_from_feature(
                                    track_feature,
                                    line_layer,
                                    densify_enabled=densify_curves,
                                    interval_m=densify_interval_m,
                                    extend_enabled=extend_curves,
                                    extend_m=extend_curves_m
                                )

                                for line in lines:
                                    lsg_line = ET.SubElement(pfd_element, 'LSG', {'A': '5', 'B': track_name})
                                    for pt in line:
                                        # v3 kennt kein PointType 6/7/9 (Guidance Reference A/B, Guidance
                                        # Point) - diese Typen gibt es erst ab v4. In v3 ist fuer normale
                                        # Stuetzpunkte einer Linie nur "2" (other) zulaessig.
                                        ET.SubElement(lsg_line, 'PNT', {'A': '2', 'C': _fmt_coord(pt.y()), 'D': _fmt_coord(pt.x())})
                        else:
                            if use_segments:
                                line_fmap = _field_map(line_layer)              # lowercase -> echter Feldname
                                seg_attr  = _pick_field(line_fmap, "Segment")   # egal ob Segment/segment/SEGMENT/SegMent/...
                                id_attr   = _pick_field(line_fmap, "ID", "field_id")  # egal ob ID/id/Id/FIELD_ID/...
                                name_attr = _pick_field(line_fmap, "Name")      # optional, falls du Name auch robust willst
                                segments = {}
                                non_segment = []
                                for track_feature in line_layer.getFeatures():
                                    if id_attr is None:
                                        continue  # ohne Zuordnungsfeld kann man nicht filtern

                                    raw = track_feature[id_attr]
                                    try:
                                        if int(raw) != int(field_id):
                                            continue
                                    except Exception:  # nosec B112
                                        continue
                                    if seg_attr is None:
                                        segments.setdefault('Kontur', []).append(track_feature)
                                    else:
                                        val = track_feature[seg_attr]
                                        if _is_nullish(val):
                                            non_segment.append(track_feature)
                                        else:
                                            label = str(val).strip()
                                            segments.setdefault(label, []).append(track_feature)
                                for seg_label, feats in segments.items():
                                    ggp_element = ET.SubElement(pfd_element, 'GGP', {
                                        'A': next_ggp_id(),
                                        'B': f'{seg_label}'})
                                    for track_feature in feats:
                                        lines = _export_lines_from_feature(
                                            track_feature,
                                            line_layer,
                                            densify_enabled=densify_curves,
                                            interval_m=densify_interval_m,
                                            extend_enabled=extend_curves,
                                            extend_m=extend_curves_m
                                        )

                                        for line in lines:
                                            c_value = '3' if len(line) > 2 else '1'
                                            gpn_element = ET.SubElement(ggp_element, 'GPN', {
                                                'A': next_gpn_id(),
                                                'B': track_feature['Name'],
                                                'C': c_value,
                                                'E': '1',
                                                'F': '1'
                                            })

                                            inner_lsg = ET.SubElement(gpn_element, 'LSG', {'A': '5'})
                                            for i, pt in enumerate(line):
                                                a_val = '6' if i == 0 else ('7' if i == len(line)-1 else '9')
                                                ET.SubElement(inner_lsg, 'PNT', {'A': a_val, 'C': _fmt_coord(pt.y()), 'D': _fmt_coord(pt.x())})
                                for track_feature in non_segment:
                                    lines = _export_lines_from_feature(
                                        track_feature,
                                        line_layer,
                                        densify_enabled=densify_curves,
                                        interval_m=densify_interval_m,
                                        extend_enabled=extend_curves,
                                        extend_m=extend_curves_m
                                    )
                                    track_name = track_feature['Name'] if 'Name' in line_names else ''
                                    ggp_extra = ET.SubElement(pfd_element, 'GGP', {
                                        'A': next_ggp_id(),
                                        'B': track_name
                                    })

                                    for line in lines:
                                        c_value = '3' if len(line) > 2 else '1'
                                        gpn_extra = ET.SubElement(ggp_extra, 'GPN', {
                                            'A': next_gpn_id(),
                                            'B': track_name,
                                            'C': c_value
                                        })

                                        inner_lsg_extra = ET.SubElement(gpn_extra, 'LSG', {'A': '5'})
                                        for i, pt in enumerate(line):
                                            a_val = '6' if i == 0 else ('7' if i == len(line)-1 else '9')
                                            ET.SubElement(inner_lsg_extra, 'PNT', {'A': a_val, 'C': _fmt_coord(pt.y()), 'D': _fmt_coord(pt.x())})
                            else:
                                line_fmap = _field_map(line_layer)
                                id_attr   = _pick_field(line_fmap, "ID", "field_id")
                                seg_attr  = _pick_field(line_fmap, "Segment")
                                name_attr = _pick_field(line_fmap, "Name")

                                for track_feature in line_layer.getFeatures():
                                    if id_attr is None:
                                        continue

                                    try:
                                        if int(track_feature[id_attr]) != int(field_id):
                                            continue
                                    except Exception:  # nosec B112
                                        continue

                                    # Wenn Kontursegmente NICHT exportiert werden:
                                    # Spuren mit gefülltem Segment-Feld ignorieren
                                    if seg_attr is not None:
                                        seg_val = track_feature[seg_attr]
                                        if not _is_nullish(seg_val):
                                            continue

                                    track_name = str(track_feature[name_attr]).strip() if name_attr else ''

                                    lines = _export_lines_from_feature(
                                        track_feature,
                                        line_layer,
                                        densify_enabled=densify_curves,
                                        interval_m=densify_interval_m,
                                        extend_enabled=extend_curves,
                                        extend_m=extend_curves_m
                                    )

                                    if not lines:
                                        continue

                                    ggp_element = ET.SubElement(pfd_element, 'GGP', {
                                        'A': next_ggp_id(),
                                        'B': track_name
                                    })

                                    for line in lines:
                                        c_value = '3' if len(line) > 2 else '1'
                                        gpn_element = ET.SubElement(ggp_element, 'GPN', {
                                            'A': next_gpn_id(),
                                            'B': track_name,
                                            'C': c_value
                                        })
                                        lsg_element_ = ET.SubElement(gpn_element, 'LSG', {'A': '5'})
                                        for i, pt in enumerate(line):
                                            a_val = "6" if i == 0 else ("7" if i == len(line) - 1 else "9")
                                            ET.SubElement(lsg_element_, 'PNT', {
                                                'A': a_val,
                                                'C': _fmt_coord(pt.y()),
                                                'D': _fmt_coord(pt.x())
                                            })
                    exported_any = True

        try:
            ET.indent(root_xml, space="  ")
        except AttributeError:
            pass  # Python < 3.9: kein Pretty-Print, XML bleibt trotzdem gueltig
        pretty_xml = '<?xml version="1.0" ?>\n' + ET.tostring(root_xml, encoding="unicode")
        try:
            with open(output_file_path, "w", encoding="utf-8") as f:
                f.write(pretty_xml)
        except Exception as e:
            self.iface.messageBar().pushMessage(
                _tr("Fehler"), _tr("Konnte XML nicht schreiben: {e}").format(e=e), level=Qgis.Critical, duration=6)
            return

        if not exported_any:
            self.iface.messageBar().pushMessage(
                _tr("Hinweis"),
                _tr("Keine passenden Gruppen/Layer gefunden – leere TASKDATA.XML geschrieben."),
                level=Qgis.Info, duration=6)
        else:
            self.iface.messageBar().pushMessage(
                _tr("Erfolgreich"), _tr("TASKDATA.XML geschrieben: {path}").format(path=output_file_path),
                level=Qgis.Success, duration=4)
        self.dlg.accept()
