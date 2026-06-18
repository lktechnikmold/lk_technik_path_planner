# -*- coding: utf-8 -*-
"""
LK-Technik Path Planner – combined QGIS Plugin (Import & Export)

Dieses Plugin vereint Import und Export von ISOXML-Daten:
- Import (ISOXML/Gen4 → QGIS)
- Export (QGIS → ISOXML/Gen4)

Entwickelt für QGIS 3.x

Copyright (C) 2024–2025  Florian Köck, LK-Technik Mold
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
Version: 1.4.0
Date: 2026-06-18
"""


import os, os.path, math, csv, xml.etree.ElementTree as ET, xml.dom.minidom
import processing

from qgis.PyQt.QtCore import Qt, QCoreApplication, QVariant, QUrl, QUrlQuery
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
    from .john_deere_gen4_import import import_john_deere_gen4
except Exception:
    from john_deere_gen4_import import import_john_deere_gen4

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsField, QgsFields, QgsFeature, QgsGeometry, QgsPointXY,
    QgsLayerTreeGroup, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsVectorFileWriter,
    QgsFeatureSink, QgsWkbTypes
)

def _tr(message: str) -> str:
    return QCoreApplication.translate('LK-Technik Path Planner', message)

def _is_nullish(v):
    if v is None:
        return True
    try:
        if isinstance(v, float) and math.isnan(v):
            return True
    except Exception:
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


# ============================================================
# NEU: Felder-Katalog (Felder.csv)
# ------------------------------------------------------------
# Felder.csv ist die "Source of Truth" für die Felder eines Betriebs.
# Spalten: id;Name
# Jeder Eintrag definiert ein Feld; Feldgrenzen, Fahrspuren und
# Hindernisse referenzieren das Feld über die Spalte ID == id.
# Dadurch können Felder ohne Feldgrenze und mehrere Feldgrenzen pro
# Feld existieren, ohne beim Export verloren zu gehen.
# ============================================================

FELDER_LAYER_NAME = "Felder"
FELDER_CSV_NAME = "Felder.csv"
FELDER_CSV_DELIM = ";"  # semikolon = Excel-freundlich (v.a. unter DE-Locale)


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
            except Exception:
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
                except Exception:
                    continue
                name = rec[name_idx].strip() if name_idx < len(rec) else ""
                rows[fid] = name
    except Exception:
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
    """Findet einen direkten Kind-Layer einer Gruppe anhand des Namens."""
    if not isinstance(group, QgsLayerTreeGroup):
        return None
    for node in group.children():
        try:
            lyr = node.layer()
        except Exception:
            lyr = None
        if isinstance(lyr, QgsVectorLayer) and lyr.name() == name:
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

    # Ergänzen/Fallback aus Feldgrenzen (ohne vorhandene Namen zu überschreiben)
    poly_layer = _find_child_layer(frm_group, "Feldgrenzen")
    if poly_layer is not None:
        fmap = _field_map(poly_layer)
        id_f = _pick_field(fmap, "ID")
        name_f = _pick_field(fmap, "Name")
        for feat in poly_layer.getFeatures():
            try:
                fid = int(feat[id_f]) if id_f else int(feat.id())
            except Exception:
                continue
            if fid not in catalog or not catalog.get(fid):
                catalog[fid] = (str(feat[name_f]).strip() if name_f else "") or catalog.get(fid, "")

    return [(fid, catalog[fid]) for fid in sorted(catalog.keys())]


class AddFarmDialog(QDialog):
    def __init__(self, customers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Betrieb hinzufügen")
        self.setMinimumWidth(420)

        layout = QFormLayout(self)

        self.cmb_customer = QComboBox()
        self.cmb_customer.addItems(customers)

        self.edit_farm_name = QLineEdit()

        layout.addRow("Kunde auswählen:", self.cmb_customer)
        layout.addRow("Betriebsname:", self.edit_farm_name)

        crs_group = QGroupBox("KBS")
        crs_row = QHBoxLayout(crs_group)

        self.rb_wgs84 = QRadioButton("WGS84 - EPSG:4326")
        self.rb_project = QRadioButton("Projekt-KBS")
        self.rb_wgs84.setChecked(True)

        self.crs_buttons = QButtonGroup(self)
        self.crs_buttons.addButton(self.rb_wgs84)
        self.crs_buttons.addButton(self.rb_project)

        crs_row.addWidget(self.rb_wgs84)
        crs_row.addWidget(self.rb_project)
        crs_row.addStretch(1)

        layout.addRow(crs_group)

        btn_row = QHBoxLayout()
        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Abbrechen")
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
        self.setWindowTitle("Feld hinzufügen")
        self.setMinimumWidth(420)
        self._pairs = list(farm_pairs)

        layout = QFormLayout(self)

        self.cmb_farm = QComboBox()
        for ctr, frm in self._pairs:
            self.cmb_farm.addItem(f"{ctr} / {frm}")

        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("z.B. Hausacker")

        layout.addRow("Betrieb:", self.cmb_farm)
        layout.addRow("Feldname:", self.edit_name)

        hint = QLabel(
            "Es wird ein Feld ohne Feldgrenze im Katalog (Felder.csv) angelegt.\n"
            "Die vergebene ID kannst du anschließend den Fahrspuren zuweisen."
        )
        hint.setWordWrap(True)
        layout.addRow(hint)

        btn_row = QHBoxLayout()
        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Abbrechen")
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LK-Technik Path Planner")
        self.setWindowIcon(QIcon(":/isoxml/icons/logo.png"))
        self.setMinimumWidth(720)

        self.mode_import = QRadioButton("Import")
        self.mode_export = QRadioButton("Export")
        self.mode_export.setChecked(True)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_export)
        mode_row.addWidget(self.mode_import)
        mode_row.addStretch(1)
        
        logo_lbl = QLabel()
        pix = QPixmap(":/isoxml/icons/logo.png")
        if not pix.isNull():
            logo_lbl.setPixmap(pix.scaledToHeight(80, Qt.SmoothTransformation))
            logo_lbl.setToolTip("LK-Technik Path Planner")
        mode_row.addWidget(logo_lbl)

        self.stack = QStackedWidget()
        self.page_export = self._build_export_page()
        self.page_import = self._build_import_page()
        self.stack.addWidget(self.page_export)
        self.stack.addWidget(self.page_import)

        self.mode_export.toggled.connect(self._sync_mode)
        self.mode_import.toggled.connect(self._sync_mode)
        self._sync_mode()

        root = QVBoxLayout(self)
        root.addLayout(mode_row)
        root.addWidget(self.stack)
        btn_row = QHBoxLayout()
        self.run_button = QPushButton("Ausführen")
        self.cancel_button = QPushButton("Schließen")
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(self.run_button)
        btn_row.addWidget(self.cancel_button)
        root.addLayout(btn_row)
        self._updating_checks = False

    def _sync_mode(self):
        self.stack.setCurrentIndex(0 if self.mode_export.isChecked() else 1)

    def _build_export_page(self):
        w = QGroupBox("Export-Optionen")
        v = QVBoxLayout(w)

        # Output path
        path_row = QHBoxLayout()
        self.out_line = QLineEdit()
        btn = QPushButton("…")
        def _pick_file():
            dn = QFileDialog.getExistingDirectory(
                self, "Zielordner für Export wählen"
            )
            if dn:
                self.out_line.setText(dn)

        btn.clicked.connect(_pick_file)
        path_row.addWidget(QLabel("Zielordner:"))
        path_row.addWidget(self.out_line, 1)
        path_row.addWidget(btn)
        v.addLayout(path_row)

        # Exportformat / Segment-Optionen
        opt_row = QHBoxLayout()
        self.chk_v3 = QCheckBox("ISOXML v3 (sonst v4)")
        self.chk_jd_gen4 = QCheckBox("John Deere Gen4")
        self.chk_seg = QCheckBox("Kontursegmente (nur v4)")

        def _toggle_export_mode():
            """
            Regeln:
            - ISOXML v3 und John Deere Gen4 schließen sich gegenseitig aus
            - Kontursegmente nur bei ISOXML v4 erlauben
            - bei v3 oder John Deere: Kontursegmente deaktivieren + abhaken
            """

            sender = self.sender()

            # gegenseitiger Ausschluss
            if sender == self.chk_v3 and self.chk_v3.isChecked():
                self.chk_jd_gen4.blockSignals(True)
                self.chk_jd_gen4.setChecked(False)
                self.chk_jd_gen4.blockSignals(False)

            elif sender == self.chk_jd_gen4 and self.chk_jd_gen4.isChecked():
                self.chk_v3.blockSignals(True)
                self.chk_v3.setChecked(False)
                self.chk_v3.blockSignals(False)

            disable_segments = self.chk_v3.isChecked() or self.chk_jd_gen4.isChecked()
            self.chk_seg.setEnabled(not disable_segments)

            if disable_segments:
                self.chk_seg.setChecked(False)

        self.chk_v3.toggled.connect(_toggle_export_mode)
        self.chk_jd_gen4.toggled.connect(_toggle_export_mode)
        _toggle_export_mode()

        opt_row.addWidget(self.chk_v3)
        opt_row.addWidget(self.chk_jd_gen4)
        opt_row.addWidget(self.chk_seg)
        opt_row.addStretch(1)
        v.addLayout(opt_row)

        # Erweiterte Optionen (einklappbar)
        self.btn_adv_export = QToolButton()
        self.btn_adv_export.setText("Erweiterte Einstellungen")
        self.btn_adv_export.setCheckable(True)
        self.btn_adv_export.setChecked(False)
        self.btn_adv_export.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_adv_export.setArrowType(Qt.RightArrow)

        self.adv_export_widget = QWidget()
        adv_layout = QFormLayout(self.adv_export_widget)
        adv_layout.setContentsMargins(24, 4, 4, 4)

        self.chk_smooth_curves = QCheckBox("Kurven glätten")

        self.spin_smooth_iterations = QDoubleSpinBox()
        self.spin_smooth_iterations.setDecimals(0)
        self.spin_smooth_iterations.setRange(1, 10)
        self.spin_smooth_iterations.setSingleStep(1)
        self.spin_smooth_iterations.setValue(3)
        self.spin_smooth_iterations.setEnabled(False)

        self.spin_smooth_max_dev = QDoubleSpinBox()
        self.spin_smooth_max_dev.setDecimals(2)
        self.spin_smooth_max_dev.setRange(0.01, 20.0)
        self.spin_smooth_max_dev.setSingleStep(0.10)
        self.spin_smooth_max_dev.setValue(0.05)
        self.spin_smooth_max_dev.setSuffix(" m")
        self.spin_smooth_max_dev.setEnabled(False)

        def _toggle_smooth(enabled):
            self.spin_smooth_iterations.setEnabled(enabled)
            self.spin_smooth_max_dev.setEnabled(enabled)

        self.chk_smooth_curves.toggled.connect(_toggle_smooth)

        smooth_row = QHBoxLayout()
        smooth_row.addWidget(self.chk_smooth_curves)
        smooth_row.addWidget(QLabel("Iterationen:"))
        smooth_row.addWidget(self.spin_smooth_iterations)
        smooth_row.addWidget(QLabel("Max. Verschiebung:"))
        smooth_row.addWidget(self.spin_smooth_max_dev)
        smooth_row.addStretch(1)

        adv_layout.addRow(smooth_row)

        self.chk_densify_curves = QCheckBox("Kurven nach Intervall verdichten")
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

        self.chk_extend_curves = QCheckBox("Kurven an den Enden verlängern")
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
        self.tree.setHeaderLabels(["Kunde / Betrieb / Feld"])
        self.tree.setColumnCount(1)
        self.tree.setSelectionMode(QTreeWidget.NoSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        v.addWidget(QLabel("Wähle, was exportiert werden soll:"))
        v.addWidget(self.tree, 1)
        # Buttons: Kunde / Betrieb / Feld hinzufügen
        add_row = QHBoxLayout()
        self.btn_add_ctr = QPushButton("Kunde hinzufügen")
        self.btn_add_frm = QPushButton("Betrieb hinzufügen")
        self.btn_add_field = QPushButton("Feld hinzufügen")
        add_row.addWidget(self.btn_add_ctr)
        add_row.addWidget(self.btn_add_frm)
        add_row.addWidget(self.btn_add_field)
        add_row.addStretch(1)
        v.addLayout(add_row)

        return w

    def _build_import_page(self):
        w = QGroupBox("Import-Optionen")
        lay = QFormLayout(w)

        self.in_line = QLineEdit()

        btn_file = QPushButton("Datei…")
        btn_folder = QPushButton("Ordner…")

        def _pick_in_file():
            fn, _ = QFileDialog.getOpenFileName(
                self,
                "TASKDATA.XML oder MasterData.xml wählen",
                '',
                'XML (*.xml);;Alle Dateien (*)'
            )
            if fn:
                self.in_line.setText(fn)

        def _pick_in_folder():
            dn = QFileDialog.getExistingDirectory(
                self,
                "John Deere Gen4 Ordner wählen"
            )
            if dn:
                self.in_line.setText(dn)

        btn_file.clicked.connect(_pick_in_file)
        btn_folder.clicked.connect(_pick_in_folder)

        h1 = QHBoxLayout()
        h1.addWidget(self.in_line, 1)
        h1.addWidget(btn_file)
        h1.addWidget(btn_folder)

        lay.addRow(QLabel("TASKDATA.XML oder Gen4-Ordner:"), h1)
        self.out_dir_line = QLineEdit()
        btn_dir = QPushButton("…")
        def _pick_dir():
            dn = QFileDialog.getExistingDirectory(self, "Ausgabe-Ordner (optional)")
            if dn:
                self.out_dir_line.setText(dn)
        btn_dir.clicked.connect(_pick_dir)
        h2 = QHBoxLayout(); h2.addWidget(self.out_dir_line, 1); h2.addWidget(btn_dir)
        lay.addRow(QLabel("Ausgabe Ordner (GPKG, optional):"), h2)

        #CRS-Auswahl
        crs_group = QGroupBox("Koordinatensystem für GPKG (Import)")
        crs_row = QHBoxLayout(crs_group)
        self.rb_import_wgs84 = QRadioButton("WGS 84 – EPSG:4326")
        self.rb_import_project = QRadioButton("Projekt-KBS")
        self.rb_import_wgs84.setChecked(True)  # Default
        self.rb_import_wgs84.setToolTip("Geometrien als WGS84 speichern (empfohlen).")
        self.rb_import_project.setToolTip("Geometrien ins aktuelle Projekt-KBS transformieren und so speichern.")
        crs_row.addWidget(self.rb_import_wgs84)
        crs_row.addWidget(self.rb_import_project)
        crs_row.addStretch(1)
        lay.addRow(crs_group)

        lay.addRow(QLabel("Hinweis: Ohne Ausgabe-Ordner werden die Layer als Temporärlayer geladen und können nicht direkt wieder exportiert werden!"))
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
                # NEU: Felder aus dem Katalog (Felder.csv) statt nur aus Feldgrenzen.
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
        self.actions = []
        self.menu = _tr('&LK-Technik Path Planner')
        self.first_start = True
        # NEU: Felder.csv-Automatik
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

        filename = style_map.get(layer_name)
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
                    "Style-Warnung",
                    f"Style für Layer '{layer.name()}' konnte nicht geladen werden: {msg}",
                    level=Qgis.Warning,
                    duration=4
                )

            layer.triggerRepaint()

            try:
                self.iface.layerTreeView().refreshLayerSymbology(layer.id())
            except Exception:
                pass

        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Style-Fehler",
                f"Fehler beim Laden des Styles für '{layer.name()}': {e}",
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
        Vergibt die Farbe anhand der Position des Betriebs innerhalb des Kunden.
        Dadurch haben Geschwisterbetriebe unterschiedliche Farben.
        """
        palette = self._betrieb_palette()

        if not isinstance(frm_group, QgsLayerTreeGroup):
            return palette[0]

        ctr_group = frm_group.parent()
        if not isinstance(ctr_group, QgsLayerTreeGroup):
            return palette[0]

        farm_groups = [
            ch for ch in ctr_group.children()
            if isinstance(ch, QgsLayerTreeGroup)
        ]

        # stabil sortieren nach Name
        farm_groups = sorted(farm_groups, key=lambda g: _norm_name(g.name()).lower())

        for idx, grp in enumerate(farm_groups):
            if grp == frm_group:
                return palette[idx % len(palette)]

        return palette[0]

    def _apply_feldgrenzen_color(self, layer: QgsVectorLayer, frm_group: QgsLayerTreeGroup):
        """
        Überschreibt nur bei Feldgrenzen die Füllfarbe des Styles.
        Die Farbe wird aus der Position des Betriebs innerhalb des Kunden vergeben.
        """
        if not layer or layer.name() != "Feldgrenzen":
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
            except Exception:
                pass

        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Farb-Fehler",
                f"Farbe für Feldgrenzen konnte nicht gesetzt werden: {e}",
                level=Qgis.Warning,
                duration=4
            )
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
                layer_nodes.append((lyr.name(), ch))

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

        # NEU: Neu hinzugefügte Feldgrenzen-Layer automatisch mit der
        # Felder.csv-Automatik verbinden (deckt Import, Betrieb-anlegen und
        # das erneute Öffnen gespeicherter Projekte ab).
        try:
            QgsProject.instance().layersAdded.connect(self._on_layers_added)
        except Exception:
            pass
        # bereits geladene Layer (Plugin nach Projektöffnung aktiviert)
        try:
            self._on_layers_added(list(QgsProject.instance().mapLayers().values()))
        except Exception:
            pass

    def unload(self):
        for a in self.actions:
            self.iface.removeToolBarIcon(a)
            self.iface.removePluginMenu(self.menu, a)
        try:
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
        except Exception:
            pass

    # ------------------- NEU: Felder.csv-Automatik -------------------
    def _on_layers_added(self, layers):
        for lyr in layers:
            try:
                if isinstance(lyr, QgsVectorLayer) and lyr.name() == "Feldgrenzen":
                    self._wire_feldgrenzen_layer(lyr)
            except Exception:
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
        except Exception:
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
        except Exception:
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
        except Exception:
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
            except Exception:
                pass
            return

        # Felder-Layer existiert noch nicht (Altprojekt) -> anlegen
        new_layer = _load_felder_layer(csv_path)
        if new_layer is not None:
            project.addMapLayer(new_layer, False)
            parent.insertLayer(0, new_layer)

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
                except Exception:
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
                    except Exception:
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
                # Name: gefüllter Feldgrenzen-Name wird in den Katalog übernommen.
                bname = ""
                if name_field:
                    nv = feat[name_field]
                    if not _is_nullish(nv):
                        bname = str(nv).strip()
                if bname:
                    if rows.get(fid) != bname:
                        rows[fid] = bname
                        changed = True
                elif fid not in rows:
                    rows[fid] = f"Feld {fid}"
                    changed = True

            if attr_changes:
                self._felder_guard = True
                try:
                    poly_layer.dataProvider().changeAttributeValues(attr_changes)
                    poly_layer.reload()
                    poly_layer.triggerRepaint()
                except Exception:
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
                except Exception:
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

        # NEU: Felder.csv beim Öffnen mit den Feldgrenzen abgleichen.
        # Dadurch landen neu gezeichnete Felder zuverlässig im Katalog,
        # auch wenn das Commit-Signal nicht gegriffen hat.
        self._sync_all_felder_catalogs()

        self.dlg.refresh_tree()
        root = QgsProject.instance().layerTreeRoot()
        for node in root.findLayers():
            lyr = node.layer()
            if not isinstance(lyr, QgsVectorLayer):
                continue

            self._apply_predefined_style(lyr)

            if lyr.name() == "Feldgrenzen":
                parent = node.parent()
                if isinstance(parent, QgsLayerTreeGroup):
                    self._apply_feldgrenzen_color(lyr, parent)

        self.dlg.show()
        self.dlg.exec_()

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

        dn = QFileDialog.getExistingDirectory(self.iface.mainWindow(), "Ablageordner für neue Betriebe wählen")
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
                "Abgebrochen", "Kein Ablageordner gewählt – Betrieb wurde nicht erstellt.",
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
                existing_names.add(lyr.name())

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

        # 5) Felder.csv (Feld-Katalog) – NEU
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
        name, ok = QInputDialog.getText(self.iface.mainWindow(), "Kunde hinzufügen", "Kundenname:")
        if not ok:
            return
        name = _norm_name(name)
        if not name:
            return

        root = QgsProject.instance().layerTreeRoot()
        _ = self._find_or_create_group(root, name)

        self.dlg.refresh_tree()
        self.iface.messageBar().pushMessage("OK", f"Kunde '{name}' angelegt.", level=Qgis.Success, duration=3)

    def _ui_add_farm(self):
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        customers = [ch.name() for ch in root.children() if isinstance(ch, QgsLayerTreeGroup)]

        if not customers:
            QMessageBox.information(
                self.iface.mainWindow(),
                "Hinweis",
                "Es gibt noch keinen Kunden. Bitte zuerst einen Kunden anlegen."
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
                "Fehler",
                f"Konnte Betrieb/Layers nicht erstellen: {e}",
                level=Qgis.Critical,
                duration=6
            )
            return

        self.dlg.refresh_tree()

        self.iface.messageBar().pushMessage(
            "OK",
            f"Betrieb '{frm_name}' mit Layern erstellt ({target_crs.authid()}).",
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
                "Hinweis",
                "Es gibt noch keinen Betrieb. Bitte zuerst einen Betrieb anlegen."
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
                "Nicht möglich",
                "Die Layer dieses Betriebs sind noch temporär (nicht gespeichert).\n"
                "Bitte zuerst dauerhaft als GeoPackage speichern, dann erneut versuchen."
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
                    except Exception:
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

        self.dlg.refresh_tree()
        self.iface.messageBar().pushMessage(
            "OK",
            f"Feld '{name}' angelegt (ID {new_id}). Weise diese ID den Fahrspuren zu.",
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
        act_rename = menu.addAction("Feld umbenennen…")
        chosen = menu.exec_(tree.viewport().mapToGlobal(pos))
        if chosen == act_rename:
            try:
                self._rename_field(ctr_name, frm_name, int(fid), item.text(0))
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    "Fehler", f"Umbenennen fehlgeschlagen: {e}", level=Qgis.Critical, duration=6
                )

    def _rename_field(self, ctr_name: str, frm_name: str, field_id: int, current_name: str):
        """
        Benennt ein Feld um: schreibt Felder.csv und gleicht – falls vorhanden –
        das Name-Attribut der zugehörigen Feldgrenze(n) an.
        Funktioniert auch für Felder OHNE Feldgrenze.
        """
        new_name, ok = QInputDialog.getText(
            self.iface.mainWindow(),
            "Feld umbenennen",
            f"Neuer Name für Feld (ID {field_id}):",
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
                "Nicht möglich",
                "Die Layer dieses Betriebs sind noch temporär (nicht gespeichert)."
            )
            return

        rows = _read_felder_csv(csv_path)
        rows[field_id] = new_name
        _write_felder_csv(csv_path, rows)

        # Name-Attribut der Feldgrenze(n) mit dieser ID angleichen
        poly = _find_child_layer(frm_group, "Feldgrenzen")
        if poly is not None:
            pmap = _field_map(poly)
            idf = _pick_field(pmap, "ID")
            namef = _pick_field(pmap, "Name")
            if idf and namef:
                nidx = poly.fields().indexOf(namef)
                changes = {}
                for f in poly.getFeatures():
                    v = f[idf]
                    if _is_nullish(v):
                        continue
                    try:
                        if int(v) == int(field_id):
                            changes[f.id()] = {nidx: new_name}
                    except Exception:
                        continue
                if changes and nidx >= 0:
                    self._felder_guard = True
                    try:
                        poly.dataProvider().changeAttributeValues(changes)
                        poly.reload()
                        poly.triggerRepaint()
                    except Exception:
                        pass
                    finally:
                        self._felder_guard = False

        ref_layer = _find_child_layer(frm_group, "Feldgrenzen") \
            or _find_child_layer(frm_group, "Fahrspuren") \
            or _find_child_layer(frm_group, "Punkthindernis") or _find_child_layer(frm_group, "Flaechenhindernis")
        if ref_layer is not None:
            self._reload_felder_for_feldgrenzen(ref_layer, csv_path)

        self.dlg.refresh_tree()
        self.iface.messageBar().pushMessage(
            "OK", f"Feld (ID {field_id}) umbenannt in '{new_name}'.", level=Qgis.Success, duration=5
        )

    # ------------------------- IMPORT -------------------------
    def _do_import(self):
        path = self.dlg.in_line.text().strip()
        out_dir = self.dlg.out_dir_line.text().strip() or None

        if not path:
            self.iface.messageBar().pushMessage(
                "Fehler",
                "Keine Datei oder kein Ordner gewählt.",
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

            # 1) John Deere Gen4
            if os.path.exists(gen4_master):
                ok = import_john_deere_gen4(self, path, out_dir)
                if ok:
                    self.dlg.accept()
                return

            # 2) Klassisches ISOXML
            if os.path.exists(isoxml_taskdata):
                path = isoxml_taskdata
            else:
                self.iface.messageBar().pushMessage(
                    "Fehler",
                    "Im gewählten Ordner wurde weder eine MasterData.xml noch eine TASKDATA.XML gefunden.",
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
                    self.dlg.accept()
                return

        if not path:
            self.iface.messageBar().pushMessage("Fehler", "Keine ISOXML-Datei gewählt.", level=Qgis.Warning, duration=4)
            return
        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"XML-Parsing fehlgeschlagen: {e}", level=Qgis.Critical, duration=6)
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
        # NEU: Felder-Katalog je Betrieb
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
                except Exception:
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

            # >>> NEU: Cache-Key nach Namen, nicht nach ID <<<
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

            # NEU: Felder-Katalog (Felder.csv) für diesen Betrieb vorbereiten
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

            # NEU: jedes Feld (PFD) im Katalog registrieren – auch ohne Feldgrenze.
            # Vorhandenen, nicht-leeren Namen nicht durch einen leeren überschreiben.
            _cur_rows = per_frm_felder_rows.setdefault(_frm_key, {})
            _new_name = pfd_name or ""
            if numeric_id not in _cur_rows or (not _cur_rows.get(numeric_id) and _new_name):
                _cur_rows[numeric_id] = _new_name

            field_layer = frm_layers["Feldgrenzen"]; line_layer = frm_layers["Fahrspuren"]
            point_layer = frm_layers["Punkthindernis"]; area_layer = frm_layers["Flaechenhindernis"]
            dp_field = field_layer.dataProvider(); dp_line = line_layer.dataProvider()
            dp_point = point_layer.dataProvider(); dp_area = area_layer.dataProvider()

            # Boundary + Area obstacles
            pln = pfd.find("PLN")
            if pln is not None:
                lsg_field = pln.find("LSG[@A='1']")
                if lsg_field is not None:
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
                        final_area_val = area_val

                        if not final_area_val or final_area_val <= 0:
                            calc_area = _calc_area_from_ring_wgs84(ring_pts_wgs84)
                            if calc_area > 0:
                                final_area_val = calc_area

                        feat_f = QgsFeature(field_layer.fields())
                        feat_f.setAttribute("ID", numeric_id)
                        feat_f.setAttribute("Name", pfd_name)
                        feat_f.setAttribute("Flaeche", final_area_val)
                        feat_f.setGeometry(QgsGeometry.fromPolygonXY([ring_pts]))
                        dp_field.addFeatures([feat_f])
                for lsg_area in pln.findall("LSG"):
                    if lsg_area.get("A") == "2":
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
                    hind_name = pnt_h.get("B", ""); bf_val = 0 if a_attr == "5" else 1
                    feat_pt = QgsFeature(point_layer.fields())
                    feat_pt.setAttribute("ID", numeric_id); feat_pt.setAttribute("Name", hind_name); feat_pt.setAttribute("befahrbar", bf_val)
                    feat_pt.setGeometry(QgsGeometry.fromPointXY(_tx_pt_xy(lon, lat)))
                    dp_point.addFeatures([feat_pt])

            # Swaths
            if is_v3:
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
            else:
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

        # NEU: Felder-Katalog je Betrieb schreiben / füllen
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
                if isinstance(felder_layer, QgsVectorLayer):
                    try:
                        felder_layer.reload()
                    except Exception:
                        pass
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

        self.iface.messageBar().pushMessage("Success", "ISOXML importiert (CTR → FRM → Layer).", level=Qgis.Success, duration=4)
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

                        if lyr.name() not in required_names:
                            continue

                        if lyr.providerType() == "memory":
                            problems.append(f"{ctr_name} / {frm_name} / {lyr.name()}")

            return problems

        memory_problems = _find_required_memory_layers()
        if memory_problems:
            preview = "\n".join(memory_problems[:8])
            if len(memory_problems) > 8:
                preview += f"\n… und {len(memory_problems) - 8} weitere"

            self.iface.messageBar().pushMessage(
                "Export nicht möglich",
                "Folgende exportrelevante Layer sind noch temporär:\n"
                f"{preview}\n\n"
                "Bitte diese Layer zuerst dauerhaft speichern.",
                level=Qgis.Warning,
                duration=10
            )
            return


        out_dir = self.dlg.out_line.text().strip()
        is_john_deere = self.dlg.chk_jd_gen4.isChecked()
        is_v3 = self.dlg.chk_v3.isChecked()
        use_segments = (self.dlg.chk_seg.isChecked() and not is_v3 and not is_john_deere)
        smooth_curves = self.dlg.chk_smooth_curves.isChecked()
        smooth_iterations = int(self.dlg.spin_smooth_iterations.value())
        smooth_max_dev = float(self.dlg.spin_smooth_max_dev.value())
        densify_curves = self.dlg.chk_densify_curves.isChecked()
        densify_interval_m = float(self.dlg.spin_densify_interval.value())
        extend_curves = self.dlg.chk_extend_curves.isChecked()
        extend_curves_m = float(self.dlg.spin_extend_curves.value())

        if not out_dir:
            self.iface.messageBar().pushMessage(
                "Fehler", "Bitte Zielordner wählen.",
                level=Qgis.Warning, duration=4
            )
            return

        selected = self.dlg.selected_export_map()
        if not selected:
            self.iface.messageBar().pushMessage(
                "Hinweis", "Keine Auswahl getroffen.",
                level=Qgis.Info, duration=4
            )
            return

        if is_john_deere:
            try:
                ok = export_john_deere_gen4(self, out_dir, selected)
                if ok:
                    self.iface.messageBar().pushMessage(
                        "Erfolgreich",
                        f"John Deere Gen4 Export erstellt: {out_dir}",
                        level=Qgis.Success,
                        duration=4
                    )
                    self.dlg.accept()
                return
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    "Fehler",
                    f"John Deere Gen4 Export fehlgeschlagen: {e}",
                    level=Qgis.Critical,
                    duration=6
                )
                return

        output_file_path = os.path.join(out_dir, "TASKDATA.XML")

        root_xml = ET.Element('ISO11783_TaskData', {
            "VersionMajor": "3" if is_v3 else "4",
            "VersionMinor": "0",
            "ManagementSoftwareManufacturer": "LK-Technik Mold",
            "ManagementSoftwareVersion": "1.4.0",
            "DataTransferOrigin": "1"
        })

        ctr_idx = 1
        frm_idx = 1
        pnt_global = 1
        ggp_global = 1
        gpn_global = 1

        CTR_WIDTH = 2
        FRM_WIDTH = 2
        FIELD_WIDTH = 6

        project = QgsProject.instance()

        def _make_pfd_id(ctr_num: int, frm_num: int, field_id: int) -> str:
            return f"PFD{ctr_num:0{CTR_WIDTH}d}{frm_num:0{FRM_WIDTH}d}{field_id:0{FIELD_WIDTH}d}"
        
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
            for node in group.children():
                try:
                    lyr = node.layer()
                except Exception:
                    lyr = None
                if isinstance(lyr, QgsVectorLayer) and lyr.name() == name:
                    return lyr
            return None

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
                    # NEU: ohne Feldgrenzen-Layer nur überspringen, wenn auch
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
                    except Exception:
                        pass

                    try:
                        prj_crs = QgsProject.instance().crs()
                        if prj_crs.isValid() and prj_crs.mapUnits() == Qgis.DistanceUnit.Meters:
                            return prj_crs
                    except Exception:
                        pass

                    return QgsCoordinateReferenceSystem("EPSG:32633")
                def _clone_geometry(geom):
                    try:
                        return QgsGeometry(geom)
                    except Exception:
                        return QgsGeometry(geom.constGet().clone())


                def _geometry_to_metric_32633(geom, source_layer):
                    if geom is None or geom.isEmpty():
                        return None

                    geom_copy = _clone_geometry(geom)

                    source_crs = source_layer.crs()
                    metric_crs = QgsCoordinateReferenceSystem("EPSG:32633")

                    if source_crs.isValid() and source_crs != metric_crs:
                        try:
                            ct = QgsCoordinateTransform(source_crs, metric_crs, QgsProject.instance())
                            geom_copy.transform(ct)
                        except Exception:
                            return None

                    return geom_copy


                def _geometry_from_32633_to_wgs84(geom):
                    if geom is None or geom.isEmpty():
                        return None

                    geom_copy = _clone_geometry(geom)

                    src = QgsCoordinateReferenceSystem("EPSG:32633")
                    dst = QgsCoordinateReferenceSystem("EPSG:4326")

                    try:
                        ct = QgsCoordinateTransform(src, dst, QgsProject.instance())
                        geom_copy.transform(ct)
                    except Exception:
                        return None

                    return geom_copy


                def _point_inside(p, field_geom):
                    p_geom = QgsGeometry.fromPointXY(QgsPointXY(p))
                    return field_geom.contains(p_geom) or field_geom.intersects(p_geom)


                def _push_inside(p, field_geom):
                    p_geom = QgsGeometry.fromPointXY(p)

                    if field_geom.isMultipart():
                        polygons = field_geom.asMultiPolygon()
                        ring = polygons[0][0]
                    else:
                        polygons = field_geom.asPolygon()
                        ring = polygons[0]

                    boundary = QgsGeometry.fromPolylineXY(ring)
                    nearest = boundary.nearestPoint(p_geom).asPoint()

                    dx = nearest.x() - p.x()
                    dy = nearest.y() - p.y()
                    dist = math.sqrt(dx * dx + dy * dy)

                    if dist == 0:
                        return QgsPointXY(nearest)

                    inside_push = 0.10
                    factor = (dist + inside_push) / dist

                    candidate = QgsPointXY(
                        p.x() + dx * factor,
                        p.y() + dy * factor
                    )

                    if _point_inside(candidate, field_geom):
                        return candidate

                    return QgsPointXY(nearest)


                def _smooth_geometry_direct(geom, iterations):
                    if QgsWkbTypes.isMultiType(geom.wkbType()):
                        return geom

                    pts = geom.asPolyline()

                    for _ in range(iterations):
                        if len(pts) < 2:
                            break

                        new_pts = [pts[0]]

                        for i in range(len(pts) - 1):
                            p0 = QgsPointXY(pts[i])
                            p1 = QgsPointXY(pts[i + 1])

                            q = QgsPointXY(
                                0.75 * p0.x() + 0.25 * p1.x(),
                                0.75 * p0.y() + 0.25 * p1.y()
                            )

                            r = QgsPointXY(
                                0.25 * p0.x() + 0.75 * p1.x(),
                                0.25 * p0.y() + 0.75 * p1.y()
                            )

                            new_pts.append(q)
                            new_pts.append(r)

                        new_pts.append(pts[-1])
                        pts = new_pts

                    return QgsGeometry.fromPolylineXY(pts)


                def _correct_line_before_smoothing(line, field_geom, iterations, max_dev):
                    if len(line) < 3:
                        return [QgsPointXY(p) for p in line]

                    current = [QgsPointXY(p) for p in line]
                    prep_passes = 3
                    inside_push = 0.10

                    for _ in range(prep_passes):
                        base_geom = QgsGeometry.fromPolylineXY(current)
                        smooth_geom = _smooth_geometry_direct(base_geom, iterations)

                        for i in range(1, len(current) - 1):
                            orig_p = current[i]

                            station = base_geom.lineLocatePoint(
                                QgsGeometry.fromPointXY(orig_p)
                            )

                            smooth_p = smooth_geom.interpolate(station).asPoint()
                            smooth_p = QgsPointXY(smooth_p)

                            if _point_inside(smooth_p, field_geom):
                                continue

                            dx = smooth_p.x() - orig_p.x()
                            dy = smooth_p.y() - orig_p.y()
                            dist = math.sqrt(dx * dx + dy * dy)

                            if dist == 0:
                                continue

                            move_dist = min(dist + inside_push, max_dev)

                            candidate = QgsPointXY(
                                orig_p.x() - dx / dist * move_dist,
                                orig_p.y() - dy / dist * move_dist
                            )

                            if not _point_inside(candidate, field_geom):
                                candidate = _push_inside(candidate, field_geom)

                            current[i] = candidate

                    return current


                def _smooth_single_geometry(feature, crs, iterations):
                    wkb = feature.geometry().wkbType()

                    if QgsWkbTypes.isMultiType(wkb):
                        geom_type = "MultiLineString"
                    else:
                        geom_type = "LineString"

                    layer = QgsVectorLayer(
                        "{}?crs={}".format(geom_type, crs.authid()),
                        "temp_line",
                        "memory"
                    )

                    provider = layer.dataProvider()
                    provider.addAttributes(feature.fields())
                    layer.updateFields()

                    new_feat = QgsFeature(layer.fields())
                    new_feat.setAttributes(feature.attributes())
                    new_feat.setGeometry(feature.geometry())

                    provider.addFeature(new_feat)
                    layer.updateExtents()

                    result = processing.run(
                        "native:smoothgeometry",
                        {
                            "INPUT": layer,
                            "ITERATIONS": iterations,
                            "OFFSET": 0.25,
                            "MAX_ANGLE": 180,
                            "OUTPUT": "memory:"
                        }
                    )

                    out_layer = result["OUTPUT"]

                    for f in out_layer.getFeatures():
                        return f.geometry()

                    return feature.geometry()


                def _smooth_geometry_for_export(track_feature, line_layer, polygon_layer, field_feature, iterations, max_dev):
                    """
                    Glättet Fahrspuren innerhalb der Feldgrenze.
                    Intern wird immer EPSG:32633 verwendet.
                    Rückgabe ist WGS84-Geometrie.
                    """

                    geom = track_feature.geometry()
                    if geom is None or geom.isEmpty():
                        return None

                    track_geom_32633 = _geometry_to_metric_32633(geom, line_layer)
                    field_geom_32633 = _geometry_to_metric_32633(field_feature.geometry(), polygon_layer)

                    if track_geom_32633 is None or field_geom_32633 is None:
                        return None

                    field_parts = []

                    if field_geom_32633.isMultipart():
                        for poly in field_geom_32633.asMultiPolygon():
                            field_parts.append(QgsGeometry.fromPolygonXY(poly))
                    else:
                        poly = field_geom_32633.asPolygon()
                        if poly:
                            field_parts.append(QgsGeometry.fromPolygonXY(poly))

                    if not field_parts:
                        return None

                    field_union = QgsGeometry.unaryUnion(field_parts)

                    if QgsWkbTypes.isMultiType(track_geom_32633.wkbType()):
                        lines = track_geom_32633.asMultiPolyline()
                        corrected_lines = []

                        for line in lines:
                            corrected = _correct_line_before_smoothing(
                                line,
                                field_union,
                                iterations,
                                max_dev
                            )
                            corrected_lines.append(corrected)

                        corrected_geom = QgsGeometry.fromMultiPolylineXY(corrected_lines)

                    else:
                        line = track_geom_32633.asPolyline()

                        corrected_line = _correct_line_before_smoothing(
                            line,
                            field_union,
                            iterations,
                            max_dev
                        )

                        corrected_geom = QgsGeometry.fromPolylineXY(corrected_line)

                    temp_feat = QgsFeature(track_feature)
                    temp_feat.setGeometry(corrected_geom)

                    final_geom_32633 = _smooth_single_geometry(
                        temp_feat,
                        QgsCoordinateReferenceSystem("EPSG:32633"),
                        iterations
                    )

                    return _geometry_from_32633_to_wgs84(final_geom_32633)

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
                    except Exception:
                        pass

                    # dann versuchen: einzelne Line
                    try:
                        line = geom.asPolyline()
                        if line:
                            return [line]
                    except Exception:
                        pass

                    return []

                def _export_lines_from_feature(
                    track_feature,
                    line_layer,
                    field_feature=None,
                    polygon_layer=None,
                    smooth_enabled=False,
                    smooth_iterations=3,
                    smooth_max_dev=0.5,
                    densify_enabled=False,
                    interval_m=1.0,
                    extend_enabled=False,
                    extend_m=0.0
                ):
                    """
                    Gibt exportierbare Linien als Liste von Polylinien in WGS84 zurück.
                    Optional:
                    - Glätten nur bei Kurven (>2 Punkte)
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

                    # 0) optional glätten
                    if smooth_enabled and has_curve and field_feature is not None and polygon_layer is not None:
                        smoothed_geom = _smooth_geometry_for_export(
                            track_feature,
                            line_layer,
                            polygon_layer,
                            field_feature,
                            smooth_iterations,
                            smooth_max_dev
                        )

                        if smoothed_geom is not None and not smoothed_geom.isEmpty():
                            working_geom = smoothed_geom

                            # WICHTIG:
                            # Ab hier liegt die Geometrie bereits in WGS84.
                            working_layer = QgsVectorLayer(
                                "MultiLineString?crs=EPSG:4326",
                                "temp_wgs84_lines",
                                "memory"
                            )

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
                        (smooth_enabled and has_curve)
                        or (densify_enabled and has_curve)
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

                # NEU: Export läuft über den Feld-Katalog (Felder.csv) statt über
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
                        except Exception:
                            continue
                        boundary_by_id.setdefault(_bid, []).append(_bf)

                for field_id, cat_name in catalog:
                    if (field_ids_filter is not None) and (field_id not in field_ids_filter):
                        continue

                    boundaries = boundary_by_id.get(field_id, [])
                    # field_feature wird weiter unten für die Glättung genutzt.
                    # None => Glättung an der Feldgrenze entfällt automatisch.
                    field_feature = boundaries[0] if boundaries else None

                    # Name/Fläche: Feldgrenze bevorzugt, sonst Katalogname
                    field_name = cat_name or str(field_id)
                    field_area = 0
                    if field_feature is not None:
                        if name_field:
                            _bn = field_feature[name_field]
                            if not _is_nullish(_bn):
                                field_name = str(_bn)
                        if area_field:
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

                    #Boundary – eine PLN je Feld; je Feldgrenze eine LSG (A=1).
                    pln_element = None
                    if boundaries:
                        pln_element = ET.SubElement(pfd_element, 'PLN', {
                            'A': '1', 'B': str(field_name), 'C': str(int(field_area)), 'E': f'PLN{field_id}'
                        })
                        for bf in boundaries:
                            lsg_field = ET.SubElement(pln_element, 'LSG', {'A': '1'})
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
                                        ET.SubElement(lsg_field, 'PNT', {'A': '2', 'C': str(lat), 'D': str(lon)})

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
                            except Exception:
                                continue
                            bf_val = fh_feature['befahrbar'] if 'befahrbar' in fh_names else 0
                            impass_val = "1" if bf_val == 0 else "0"
                            # Falls das Feld keine Feldgrenze hat, PLN hier nachträglich anlegen,
                            # damit Flächenhindernisse ein gültiges Eltern-Element haben.
                            if pln_element is None:
                                pln_element = ET.SubElement(pfd_element, 'PLN', {
                                    'A': '1', 'B': str(field_name), 'C': str(int(field_area)), 'E': f'PLN{field_id}'
                                })
                            lsg_hind = ET.SubElement(pln_element, 'LSG', {'A': '2', 'P094_Impassable': impass_val})
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
                                        ET.SubElement(lsg_hind, 'PNT', {'A': '10', 'C': str(lat2), 'D': str(lon2)})

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
                            except Exception:
                                continue
                            bf_val = hindernis['befahrbar'] if 'befahrbar' in p_names else 1
                            a_val = "1" if bf_val == 1 else "5"
                            hind_name = hindernis['name'] if 'name' in p_names else (hindernis['Name'] if 'Name' in p_names else '')
                            pt_geom = hindernis.geometry().asPoint()
                            lonp, latp = _to_wgs_xy_from_point(pt_geom, ct_point)
                            g_val = f"PNT{pnt_global}"; pnt_global += 1
                            ET.SubElement(pfd_element, 'PNT', {'A': a_val, 'B': hind_name, 'C': str(latp), 'D': str(lonp), 'G': g_val})

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
                                except Exception:
                                    continue

                                track_name = track_feature['Name'] if 'Name' in line_names else ''

                                lines = _export_lines_from_feature(
                                    track_feature,
                                    line_layer,
                                    field_feature=field_feature,
                                    polygon_layer=polygon_layer,
                                    smooth_enabled=smooth_curves,
                                    smooth_iterations=smooth_iterations,
                                    smooth_max_dev=smooth_max_dev,
                                    densify_enabled=densify_curves,
                                    interval_m=densify_interval_m,
                                    extend_enabled=extend_curves,
                                    extend_m=extend_curves_m
                                )

                                for line in lines:
                                    lsg_line = ET.SubElement(pfd_element, 'LSG', {'A': '5', 'B': track_name})
                                    for i, pt in enumerate(line):
                                        a_val = '6' if i == 0 else ('7' if i == len(line)-1 else '9')
                                        ET.SubElement(lsg_line, 'PNT', {'A': a_val, 'C': str(pt.y()), 'D': str(pt.x())})
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
                                    except Exception:
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
                                            field_feature=field_feature,
                                            polygon_layer=polygon_layer,
                                            smooth_enabled=smooth_curves,
                                            smooth_iterations=smooth_iterations,
                                            smooth_max_dev=smooth_max_dev,
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
                                                ET.SubElement(inner_lsg, 'PNT', {'A': a_val, 'C': str(pt.y()), 'D': str(pt.x())})
                                for track_feature in non_segment:
                                    lines = _export_lines_from_feature(
                                        track_feature,
                                        line_layer,
                                        field_feature=field_feature,
                                        polygon_layer=polygon_layer,
                                        smooth_enabled=smooth_curves,
                                        smooth_iterations=smooth_iterations,
                                        smooth_max_dev=smooth_max_dev,
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
                                            ET.SubElement(inner_lsg_extra, 'PNT', {'A': a_val, 'C': str(pt.y()), 'D': str(pt.x())})
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
                                    except Exception:
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
                                        field_feature=field_feature,
                                        polygon_layer=polygon_layer,
                                        smooth_enabled=smooth_curves,
                                        smooth_iterations=smooth_iterations,
                                        smooth_max_dev=smooth_max_dev,
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
                                                'C': str(pt.y()),
                                                'D': str(pt.x())
                                            })
                    exported_any = True

        xml_bytes = ET.tostring(root_xml, encoding='utf-8')
        dom = xml.dom.minidom.parseString(xml_bytes)
        pretty_xml = dom.toprettyxml(indent="  ")
        try:
            with open(output_file_path, "w", encoding="utf-8") as f:
                f.write(pretty_xml)
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Konnte XML nicht schreiben: {e}", level=Qgis.Critical, duration=6)
            return

        if not exported_any:
            self.iface.messageBar().pushMessage("Hinweis", "Keine passenden Gruppen/Layer gefunden – leere TASKDATA.XML geschrieben.", level=Qgis.Info, duration=6)
        else:
            self.iface.messageBar().pushMessage("Erfolgreich", f"TASKDATA.XML geschrieben: {output_file_path}", level=Qgis.Success, duration=4)
        self.dlg.accept()
