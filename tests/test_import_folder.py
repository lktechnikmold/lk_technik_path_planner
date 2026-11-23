# -*- coding: utf-8 -*-
"""
Ordner-Import: alle Lenksystemdateien im Ordner finden, per Kontrollkästchen
auswählen und je Kunde/Betrieb zusammenführen.
"""

import importlib
import os
import shutil
import xml.etree.ElementTree as ET  # nosec B405 - nur selbst erzeugte Testdateien
import zipfile

from qgis.core import QgsVectorLayer

from support import (LONG_NAME, PACKAGE, PluginTestCase, catalog, clear_project, features, groups, ll, metric,
                     read_text, rect)

scan = importlib.import_module(f"{PACKAGE}.import_scan")
F = importlib.import_module(f"{PACKAGE}.agco_kml_format")

A = ("Testkunde A", "Testbetrieb A")
B = ("Testkunde B", "Testbetrieb B")
UNNAMED = ("Unbenannter Kunde", "Unbenannter Betrieb")
FIELDS_A = ["Rechteck mit Loch", "Zwei Teile", LONG_NAME]
FIELDS_B = ["Flache Kurve", "Streifen"]


def write_isoxml_without_farm(path, name):
    """ISOXML mit einem Feld ohne Kunde und Betrieb (PFD ohne E/F)."""
    root = ET.Element("ISO11783_TaskData", {"VersionMajor": "4", "VersionMinor": "3",
                                            "ManagementSoftwareManufacturer": "Test", "DataTransferOrigin": "1"})
    pfd = ET.SubElement(root, "PFD", {"A": "PFD1", "C": name, "D": "1"})
    pln = ET.SubElement(pfd, "PLN", {"A": "1", "C": "1", "E": "PLN1"})
    lsg = ET.SubElement(pln, "LSG", {"A": "1"})
    for x, y in rect(0, 3000, 100, 3100):
        la, lo = ll(x, y)
        ET.SubElement(lsg, "PNT", {"A": "2", "C": f"{la:.9f}", "D": f"{lo:.9f}"})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def data_layers(key):
    """Anzahl der Layer je Art in einer Betriebsgruppe (es darf nur einen Satz geben)."""
    counts = {}
    for ch in groups()[key].children():
        lyr = ch.layer() if hasattr(ch, "layer") else None
        if isinstance(lyr, QgsVectorLayer):
            name = lyr.name()
            counts[name] = counts.get(name, 0) + 1
    return counts


class TestImportFolder(PluginTestCase):

    def setUp(self):
        super().setUp()
        self.import_test_data()
        # ein "USB-Stick" mit allen Formaten derselben Testdaten
        self.stick = os.path.join(self.tmp, "stick")
        for brand, model in (("ISOXML", "v4"), ("John Deere", "GS4 / GS5"),
                             ("Trimble", "CFX / FM / FMX / GFX / TMX"), ("CN1", "CNH"), ("AGCO", "KML")):
            self.export(brand, model, out=self.stick)
        clear_project()

    def kinds(self, entries):
        out = {}
        for e in entries:
            out[e["kind"]] = out.get(e["kind"], 0) + 1
        return out

    def test_scan_finds_all_formats(self):
        # Fendt-Export entpackt daneben: die TASKDATA dort gehört zur KML, die KML ist eine Kopie
        fendt = os.path.join(self.stick, "Fendt")
        with zipfile.ZipFile(os.path.join(self.stick, "Field_Streifen.zip")) as zf:
            zf.extractall(fendt)
        write_isoxml_without_farm(os.path.join(fendt, "TASKDATA", "TASKDATA.XML"), "Streifen")
        entries = scan.find_datasets(self.stick)
        self.assertEqual(self.kinds(entries), {"ISOXML": 1, "Gen4": 1, "AgGPS": 1, "CN1": 1, "KML": 5})
        kml_labels = sorted(e["label"] for e in entries if e["kind"] == "KML")
        self.assertEqual(kml_labels, sorted(FIELDS_A + FIELDS_B))
        self.assertEqual(next(e["rel"] for e in entries if e["kind"] == "ISOXML"),
                         os.path.join("TASKDATA", "TASKDATA.XML"))

    def test_selection_and_merge(self):
        msg = self.import_path(self.stick, files=lambda e: e["kind"] in ("ISOXML", "KML"))
        self.assertEqual(len(self.file_choices), 1, "Auswahldialog wurde gezeigt")
        self.assertEqual(len(self.file_choices[0]), 9)
        self.assertIn("6 Datei(en) importiert, 3 Betrieb(e)", msg)
        self.assertEqual(set(groups()), {A, B, UNNAMED})
        self.assertEqual(sorted(catalog(*A).values()), sorted(FIELDS_A))
        self.assertEqual(sorted(catalog(*UNNAMED).values()), sorted(FIELDS_A + FIELDS_B))
        for key in (A, B, UNNAMED):
            self.assertEqual(set(data_layers(key).values()), {1}, f"genau ein Layersatz: {key}")

    def test_same_farm_from_two_files(self):
        self.import_path(self.stick, files=lambda e: e["kind"] in ("ISOXML", "Gen4"))
        self.assertEqual(set(groups()), {A, B})
        cat = catalog(*A)
        self.assertEqual(sorted(cat), [1, 2, 3, 4, 5, 6], "Feld-IDs fortlaufend neu vergeben")
        self.assertEqual(sorted(cat.values()), sorted(FIELDS_A * 2))
        self.assertEqual(set(data_layers(A).values()), {1})
        # jede Feld-ID hat ihre eigene Grenze, beide Kopien gleich groß
        areas = {}
        for f in features(*A, "Feldgrenzen"):
            areas.setdefault(cat[int(f["ID"])], []).append(round(metric(f.geometry()).area()))
        self.assertEqual(len(areas["Rechteck mit Loch"]), 2)
        self.assertEqual(len(set(areas["Rechteck mit Loch"])), 1)
        # gespeichert: GPKG und Felder.csv enthalten beide Dateien
        lyr = groups()[A]
        src = [ch.layer().source() for ch in lyr.children() if ch.layer().name() == "Feldgrenzen"][0]
        self.assertTrue(src.split("|")[0].lower().endswith(".gpkg"), src)
        on_disk = QgsVectorLayer(src, "check", "ogr")
        self.assertEqual(on_disk.featureCount(), len(features(*A, "Feldgrenzen")))
        csv = os.path.join(os.path.dirname(src.split("|")[0]), "Felder.csv")
        self.assertEqual(sum(1 for line in read_text(csv).splitlines()[1:] if line.strip()), 6)

    def test_files_without_farm_are_merged(self):
        write_isoxml_without_farm(os.path.join(self.stick, "Alt", "TASKDATA", "TASKDATA.XML"), "Ohne Betrieb")
        self.import_path(self.stick, files=lambda e: e["kind"] == "KML" or "Alt" in e["rel"])
        self.assertEqual(set(groups()), {UNNAMED})
        self.assertEqual(sorted(catalog(*UNNAMED).values()), sorted(FIELDS_A + FIELDS_B + ["Ohne Betrieb"]))
        self.assertEqual(len(catalog(*UNNAMED)), 6)

    def test_reimport_with_overwrite(self):
        out = os.path.join(self.tmp, "ziel")
        pick = lambda e: e["kind"] in ("ISOXML", "Gen4")  # noqa: E731
        self.import_path(self.stick, out=out, files=pick)
        n = len(features(*A, "Feldgrenzen"))
        clear_project()
        from support import QMessageBox
        self.import_path(self.stick, out=out, files=pick, answer=QMessageBox.StandardButton.Yes)
        self.assertEqual(len(self.questions), 1, "Überschreiben-Frage")
        self.assertEqual(len(features(*A, "Feldgrenzen")), n, "nicht doppelt")
        self.assertEqual(len(catalog(*A)), 6)

    def test_cancel_and_empty_selection(self):
        self.assertIn("Import abgebrochen", self.import_path(self.stick, files=False))
        self.assertEqual(groups(), {})
        self.assertIn("Keine Auswahl", self.import_path(self.stick, files=lambda e: False))
        self.assertEqual(groups(), {})

    def test_single_dataset_without_dialog(self):
        only = os.path.join(self.tmp, "nur_gen4")
        shutil.copytree(os.path.join(self.stick, "Gen4"), os.path.join(only, "Gen4"))
        self.import_path(only)
        self.assertEqual(self.file_choices, [], "kein Auswahldialog bei nur einer Datei")
        self.assertEqual(set(groups()), {A, B})
        self.assertEqual(sorted(catalog(*A).values()), sorted(FIELDS_A))

    def test_kml_only_folder_one_pass(self):
        only = os.path.join(self.tmp, "nur_kml")
        os.makedirs(only)
        for fn in os.listdir(self.stick):
            if fn.endswith(".zip"):
                shutil.copy(os.path.join(self.stick, fn), only)
        msg = self.import_path(only)
        self.assertEqual(len(self.file_choices[0]), 5)
        self.assertIn("AGCO-KML-Daten wurden importiert", msg)
        self.assertEqual(set(groups()), {UNNAMED})
        self.assertEqual(len(catalog(*UNNAMED)), 5)
