# -*- coding: utf-8 -*-
"""
Gemeinsamer Unterbau für die Tests: Plugin laden, Projekt zurücksetzen,
künstliche Testdaten erzeugen, Dialoge simulieren, Export ausführen.

Alle Testdaten sind künstlich (Rechtecke, Streifen, Kurven in einem frei
gewählten Gebiet) - es werden keine echten Kunden- oder Felddaten verwendet.
"""

import contextlib
import glob
import importlib
import math
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET  # nosec B405 - nur selbst erzeugte Testdateien
from unittest.mock import MagicMock, patch

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsLayerTreeGroup,
    QgsPointXY, QgsProject, QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.basename(PLUGIN_DIR)

mod = importlib.import_module(f"{PACKAGE}.lk_technik_path_planner")
tc = importlib.import_module(f"{PACKAGE}.track_creation")
cn1 = importlib.import_module(f"{PACKAGE}.cn1_format")
translations = importlib.import_module(f"{PACKAGE}.translations")

QDialog = mod.QDialog
QMessageBox = mod.QMessageBox
Qt = mod.Qt

# ---------------------------------------------------------------------------
# Künstliches Testgebiet: Koordinaten werden in Metern um einen Ursprung
# angegeben und mit einer einfachen Näherung in WGS84 umgerechnet.
# ---------------------------------------------------------------------------
LAT0 = 47.500000
LON0 = 14.500000
M_PER_DEG_LAT = 111320.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(LAT0))

UTM = QgsCoordinateReferenceSystem("EPSG:32633")
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

LONG_NAME = "Grünland Süd Öst ß Langname 1234"      # genau 32 Zeichen
assert len(LONG_NAME) == 32


def ll(x, y):
    """Meter (Ost, Nord) relativ zum Ursprung -> (lat, lon)."""
    return LAT0 + y / M_PER_DEG_LAT, LON0 + x / M_PER_DEG_LON


def ring(*pts):
    pts = list(pts)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def rect(x0, y0, x1, y1):
    return ring((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def arc(cx, cy, r, a0, a1, step=10):
    """Bogenpunkte (Grad, gegen den Uhrzeigersinn) um (cx, cy)."""
    n = int(abs(a1 - a0) / step)
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


# Beschreibung der Testdaten. Wird als ISOXML v4 geschrieben und ist damit die
# Ausgangsbasis für alle Import-/Export-Tests.
#   fields: (id, name, [Grenzen: [Außenring, Loch, ...]], tracks, points, areas)
#   tracks: (Segmentname oder None, [(Spurname, [Punkte])])
TEST_DATA = [
    ("Testkunde A", "Testbetrieb A", [
        dict(id=1, name="Rechteck mit Loch",
             boundaries=[[rect(0, 0, 300, 200), rect(130, 80, 170, 120)]],
             tracks=[
                 (None, [("AB Ost", [(10, 10), (290, 10)])]),
                 (None, [("Kurve West", [(10, 20), (40, 80), (30, 140), (10, 190)])]),
                 ("Kontur Nord", [("Nord Gerade", [(20, 190), (280, 190)]),
                                  ("Nord Kurve", [(280, 190), (295, 150), (290, 100), (295, 50)])]),
             ],
             points=[("Stein", 0, (100, 50)), ("Fahne", 1, (200, 150))],
             areas=[("Teich", 0, ring((220, 30), (260, 30), (260, 60), (220, 60))),
                    (None, 1, ring((40, 150), (70, 150), (70, 170), (40, 170)))]),
        dict(id=2, name="Zwei Teile",
             boundaries=[[rect(400, 0, 600, 200)], [rect(600, 0, 800, 200)]],
             tracks=[], points=[], areas=[]),
        dict(id=3, name=LONG_NAME,
             boundaries=[[rect(0, 300, 250, 450)]],
             tracks=[(None, [("Gerade 1", [(0, 300), (250, 300)])])], points=[], areas=[]),
    ]),
    ("Testkunde B", "Testbetrieb B", [
        # schmaler Streifen mit Spitze (3 m breit, 600 m lang)
        dict(id=1, name="Streifen",
             boundaries=[[ring((0, 1000), (600, 1000), (603, 1001.5), (600, 1003), (5, 1003),
                               (0, 1003))]],
             tracks=[], points=[], areas=[]),
        # Feld mit flacher Kurve, deren letztes Stück (8 m) direkt an einer Ecke endet
        dict(id=2, name="Flache Kurve",
             boundaries=[[ring((0, 1100), (300, 1100), (300, 1300),
                               *[(300 - x, 1300 + 0.0004 * x * x) for x in [*range(25, 300, 25), 292]],
                               (0, 1336))]],
             tracks=[], points=[], areas=[]),
    ]),
]


def write_isoxml(path, data=TEST_DATA):
    """Schreibt die Testdaten als ISOXML v4 (TASKDATA.XML)."""
    root = ET.Element("ISO11783_TaskData", {
        "VersionMajor": "4", "VersionMinor": "3", "ManagementSoftwareManufacturer": "Test",
        "ManagementSoftwareVersion": "1", "DataTransferOrigin": "1"})
    pln = ggp = gpn = 1
    for ci, (ctr, frm, fields) in enumerate(data, 1):
        ET.SubElement(root, "CTR", {"A": f"CTR{ci}", "B": ctr})
        ET.SubElement(root, "FRM", {"A": f"FRM{ci}", "B": frm, "I": f"CTR{ci}"})
        for f in fields:
            pfd = ET.SubElement(root, "PFD", {
                "A": f"PFD{ci:02d}01{f['id']:06d}", "C": f["name"], "D": "1",
                "E": f"CTR{ci}", "F": f"FRM{ci}"})
            for rings in f["boundaries"]:
                p = ET.SubElement(pfd, "PLN", {"A": "1", "B": f["name"], "C": "1", "E": f"PLN{pln}"})
                pln += 1
                for k, r in enumerate(rings):
                    lsg = ET.SubElement(p, "LSG", {"A": "1" if k == 0 else "2"})
                    for x, y in r:
                        lat, lon = ll(x, y)
                        ET.SubElement(lsg, "PNT", {"A": "2", "C": f"{lat:.9f}", "D": f"{lon:.9f}"})
            for name, passable, r in f["areas"]:
                attrs = {"A": "8" if passable else "6"}
                if name:
                    attrs["B"] = name
                attrs.update({"C": "0", "E": f"PLN{pln}"})
                pln += 1
                p = ET.SubElement(pfd, "PLN", attrs)
                lsg = ET.SubElement(p, "LSG", {"A": "1"})
                for x, y in r:
                    lat, lon = ll(x, y)
                    ET.SubElement(lsg, "PNT", {"A": "2", "C": f"{lat:.9f}", "D": f"{lon:.9f}"})
            for name, passable, (x, y) in f["points"]:
                lat, lon = ll(x, y)
                ET.SubElement(pfd, "PNT", {"A": "1" if passable else "5", "B": name,
                                           "C": f"{lat:.9f}", "D": f"{lon:.9f}"})
            for seg_name, tracks in f["tracks"]:
                g = ET.SubElement(pfd, "GGP", {"A": f"GGP{ggp}", "B": seg_name or tracks[0][0]})
                ggp += 1
                for tname, pts in tracks:
                    n = ET.SubElement(g, "GPN", {"A": f"GPN{gpn}", "B": tname, "C": "1" if len(pts) == 2 else "3"})
                    gpn += 1
                    lsg = ET.SubElement(n, "LSG", {"A": "5"})
                    for i, (x, y) in enumerate(pts):
                        lat, lon = ll(x, y)
                        a = "6" if i == 0 else ("7" if i == len(pts) - 1 else "9")
                        ET.SubElement(lsg, "PNT", {"A": a, "C": f"{lat:.9f}", "D": f"{lon:.9f}"})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


# ---------------------------------------------------------------------------
# Projekt / Plugin
# ---------------------------------------------------------------------------
def clear_project():
    prj = QgsProject.instance()
    for lid in list(prj.mapLayers().keys()):
        prj.removeMapLayer(lid)
    prj.layerTreeRoot().removeAllChildren()


def groups():
    """{(Kunde, Betrieb): Gruppe}"""
    out = {}
    for ctr in QgsProject.instance().layerTreeRoot().children():
        if isinstance(ctr, QgsLayerTreeGroup):
            for frm in ctr.children():
                if isinstance(frm, QgsLayerTreeGroup):
                    out[(ctr.name(), frm.name())] = frm
    return out


def layer(ctr, frm, name):
    return mod._find_child_layer(groups()[(ctr, frm)], name)


def features(ctr, frm, name, field_id=None):
    lyr = layer(ctr, frm, name)
    if lyr is None:
        return []
    out = [f for f in lyr.getFeatures()]
    if field_id is not None:
        out = [f for f in out if int(f["ID"]) == int(field_id)]
    return out


def val(v):
    """Attributwert normalisieren: NULL (QGIS 3) / None (QGIS 4) / "" -> None."""
    if v is None or str(v) in ("", "NULL") or (hasattr(v, "isNull") and v.isNull()):
        return None
    return v


def nkey(t):
    """Sortierschlüssel für Tupel mit möglichen None-Werten."""
    return tuple("" if x is None else str(x) for x in t)


def line_pts(geom):
    lines = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    return lines[0] if len(lines) == 1 else [p for pl in lines for p in pl]


def catalog(ctr, frm):
    return dict(mod._field_catalog_for_frm(groups()[(ctr, frm)]))


def metric(geom, crs=WGS84):
    g = QgsGeometry(geom)
    g.transform(QgsCoordinateTransform(crs, UTM, QgsProject.instance()))
    return g


def vertices(geom):
    return [QgsPointXY(v) for v in geom.vertices()]


class PluginTestCase(unittest.TestCase):
    """Basisklasse: frisches Projekt, Plugin mit Schein-iface, echter Dialog."""

    def setUp(self):
        clear_project()
        self.tmp = tempfile.mkdtemp(prefix="lkpp_test_")
        QgsProject.instance().setPresetHomePath(self.tmp)
        translations.set_language("de")
        self.msgs = []
        iface = MagicMock()
        iface.mainWindow.return_value = None
        iface.messageBar.return_value.pushMessage.side_effect = \
            lambda *a, **k: self.msgs.append((str(a[0]), str(a[1])))
        self.canvas = QgsMapCanvas()
        self.canvas.resize(800, 600)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        iface.mapCanvas.return_value = self.canvas
        self.plugin = mod.LkTechnikPathPlanner(iface)
        self.dlg = mod.ToolboxDialog()
        self.dlg.accept = lambda: None
        self.plugin.dlg = self.dlg

    def tearDown(self):
        clear_project()
        QgsProject.instance().setPresetHomePath("")
        translations.set_language("de")
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- Hilfen ---
    def last_msg(self):
        return self.msgs[-1][1] if self.msgs else ""

    def import_path(self, path, out_name="import", out=None, answer=None):
        """
        Import über den Dialog. Ohne out landet jeder Import in einem eigenen
        Ordner; mit out in genau diesem (answer = Antwort auf "Überschreiben?").
        Rückgabe: letzte Meldung.
        """
        self.dlg.mode_import.setChecked(True)
        self.dlg.in_line.setText(path)
        if out is None:
            out = os.path.join(self.tmp, out_name)
            n = 2
            while os.path.exists(out):
                out = os.path.join(self.tmp, f"{out_name}_{n}")
                n += 1
        self.dlg.out_dir_line.setText(out)
        with answer_dialogs(question=answer) as seen:
            self.plugin._on_run()
        self.questions = seen["question"]
        self.dlg.refresh_tree()
        return self.last_msg()

    def import_test_data(self, data=TEST_DATA):
        xml = write_isoxml(os.path.join(self.tmp, "src", "TASKDATA", "TASKDATA.XML"), data)
        msg = self.import_path(xml, "import_src")
        self.assertIn("importiert", msg)
        return xml

    def select_terminal(self, brand, model):
        cmb = self.dlg.cmb_terminal
        for i in range(cmb.count()):
            d = cmb.itemData(i)
            if d and d[0] == brand and d[1] == model:
                cmb.setCurrentIndex(i)
                return d
        raise AssertionError(f"Terminal {brand} {model} nicht in der Liste")

    def export(self, brand, model, seg=False, densify=None, extend=None, out=None, answer=None):
        """Export über den Dialog. Rückgabe: Zielordner."""
        out = out or tempfile.mkdtemp(dir=self.tmp, prefix="export_")
        self.dlg.mode_export.setChecked(True)
        self.select_terminal(brand, model)
        self.dlg.out_line.setText(out)
        self.dlg.chk_seg.setChecked(seg)
        self.dlg.chk_densify_curves.setChecked(densify is not None)
        if densify is not None:
            self.dlg.spin_densify_interval.setValue(densify)
        self.dlg.chk_extend_curves.setChecked(extend is not None)
        if extend is not None:
            self.dlg.spin_extend_curves.setValue(extend)
        self.dlg.refresh_tree()
        with answer_dialogs(question=answer):
            self.plugin._on_run()
        return out


# ---------------------------------------------------------------------------
# Dialoge simulieren
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def answer_dialogs(text=None, message_box=None, question=None, farm=None, field=None):
    """
    Ersetzt modale Dialoge durch feste Antworten:
      text        - Eingabe für QInputDialog (None = Abbrechen)
      message_box - Antwort von QMessageBox.exec() (z. B. StandardButton.Yes)
      question    - Antwort von QMessageBox.question()
      farm        - (Kunde, Betriebsname) für den Betrieb-Dialog
      field       - ((Kunde, Betrieb), Feldname) für den Feld-Dialog
    """
    seen = {"question": [], "message_box": [], "information": [], "warning": []}

    def _input_exec(self_dlg):
        if text is None:
            return QDialog.DialogCode.Rejected
        self_dlg.setTextValue(text)
        return QDialog.DialogCode.Accepted

    def _msg_exec(self_box):
        seen["message_box"].append(self_box.text())
        return message_box if message_box is not None else QMessageBox.StandardButton.No

    def _question(*a, **k):
        seen["question"].append(a[2] if len(a) > 2 else "")
        return question if question is not None else QMessageBox.StandardButton.Yes

    def _farm_exec(self_dlg):
        if farm is None:
            return QDialog.DialogCode.Rejected
        self_dlg.cmb_customer.setCurrentText(farm[0])
        self_dlg.edit_farm_name.setText(farm[1])
        self_dlg.rb_wgs84.setChecked(True)
        return QDialog.DialogCode.Accepted

    def _field_exec(self_dlg):
        if field is None:
            return QDialog.DialogCode.Rejected
        pair, name = field
        for i in range(self_dlg.cmb_farm.count()):
            if tuple(self_dlg.cmb_farm.itemData(i) or ()) == tuple(pair) or self_dlg.cmb_farm.itemText(i) == " / ".join(pair):
                self_dlg.cmb_farm.setCurrentIndex(i)
        self_dlg.edit_name.setText(name or "")
        return QDialog.DialogCode.Accepted

    with patch.object(mod.QInputDialog, "exec", _input_exec), \
            patch.object(mod.QMessageBox, "exec", _msg_exec), \
            patch.object(mod.QMessageBox, "question", staticmethod(_question)), \
            patch.object(mod.QMessageBox, "information",
                         staticmethod(lambda *a, **k: seen["information"].append(a[2] if len(a) > 2 else ""))), \
            patch.object(mod.QMessageBox, "warning",
                         staticmethod(lambda *a, **k: seen["warning"].append(a[2] if len(a) > 2 else ""))), \
            patch.object(mod.AddFarmDialog, "exec", _farm_exec), \
            patch.object(mod.AddFieldDialog, "exec", _field_exec):
        yield seen


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def find_file(folder, pattern):
    hits = glob.glob(os.path.join(folder, "**", pattern), recursive=True)
    return hits[0] if hits else None
