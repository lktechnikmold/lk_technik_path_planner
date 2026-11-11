# -*- coding: utf-8 -*-
"""Import in einen Zielordner, der schon importierte Felddaten enthält: Rückfrage Überschreiben/Abbrechen."""

import os

from qgis.core import QgsVectorLayer

from support import (TEST_DATA, PluginTestCase, QMessageBox, catalog, clear_project, features, find_file, groups,
                     mod, write_isoxml)

A = ("Testkunde A", "Testbetrieb A")
B = ("Testkunde B", "Testbetrieb B")
YES = QMessageBox.StandardButton.Yes
NO = QMessageBox.StandardButton.No


class TestImportIntoExistingFolder(PluginTestCase):

    def setUp(self):
        super().setUp()
        self.xml = write_isoxml(os.path.join(self.tmp, "src", "TASKDATA", "TASKDATA.XML"))
        self.out = os.path.join(self.tmp, "ziel")
        self.import_path(self.xml, out=self.out)
        self.assertEqual(self.questions, [], "leerer Zielordner: keine Rückfrage")

    def layer_counts(self, key):
        """Anzahl Layer je Typ in der Betriebsgruppe (darf nie > 1 sein)."""
        counts = {}
        for ch in groups()[key].children():
            lyr = ch.layer() if hasattr(ch, "layer") else None
            if lyr is not None:
                name = mod.translations.canonical_layer_name(lyr.name())
                counts[name] = counts.get(name, 0) + 1
        return counts

    def file_count(self, key, name):
        return QgsVectorLayer(os.path.join(self.out, *key, name + ".gpkg"), "x", "ogr").featureCount()

    def test_cancel_keeps_everything(self):
        msg = self.import_path(self.xml, out=self.out, answer=NO)
        self.assertEqual(len(self.questions), 1)
        self.assertIn("Import abgebrochen", msg)
        self.assertEqual(len(features(*A, "Feldgrenzen")), 4)
        self.assertEqual(self.file_count(A, "Feldgrenzen"), 4)
        self.assertTrue(all(n == 1 for n in self.layer_counts(A).values()), self.layer_counts(A))

    def test_overwrite_replaces_without_duplicates(self):
        msg = self.import_path(self.xml, out=self.out, answer=YES)
        self.assertIn("importiert", msg)
        for key in (A, B):
            self.assertTrue(all(n == 1 for n in self.layer_counts(key).values()), self.layer_counts(key))
            self.assertEqual(set(self.layer_counts(key)),
                             {"Felder", "Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"})
        self.assertEqual(len(features(*A, "Feldgrenzen")), 4)
        self.assertEqual(self.file_count(A, "Feldgrenzen"), 4)
        self.assertEqual(self.file_count(A, "Fahrspuren"), 5)
        self.assertEqual(catalog(*A)[1], "Rechteck mit Loch")

    def test_overwrite_only_farms_in_import(self):
        # zweiter Import enthält nur Kunde A, Feld 1 umbenannt und ohne Spuren
        data_a = [(TEST_DATA[0][0], TEST_DATA[0][1],
                   [dict(TEST_DATA[0][2][0], name="Neuer Name", tracks=[])])]
        xml2 = write_isoxml(os.path.join(self.tmp, "src2", "TASKDATA", "TASKDATA.XML"), data_a)
        b_before = len(features(*B, "Feldgrenzen"))
        self.import_path(xml2, out=self.out, answer=YES)
        self.assertEqual(catalog(*A), {1: "Neuer Name"})
        self.assertEqual(features(*A, "Fahrspuren"), [])
        self.assertEqual(self.file_count(A, "Feldgrenzen"), 1)
        # Kunde B bleibt unverändert (im Projekt und in den Dateien)
        self.assertEqual(len(features(*B, "Feldgrenzen")), b_before)
        self.assertEqual(self.file_count(B, "Feldgrenzen"), b_before)
        self.assertEqual(catalog(*B), {1: "Streifen", 2: "Flache Kurve"})

    def test_other_formats_overwrite(self):
        exports = {
            "Gen4": (("John Deere", "GS4"), lambda o: os.path.dirname(find_file(o, "MasterData.xml"))),
            "AgGPS": (("Trimble", "CFX 750"), lambda o: os.path.join(o, "AgGPS")),
            "CN1": (("CN1", "CNH"), lambda o: o),
        }
        for fmt, ((brand, model), src) in exports.items():
            with self.subTest(format=fmt):
                clear_project()
                self.import_path(self.xml, out=self.out, answer=YES)
                exp = src(self.export(brand, model))
                clear_project()
                target = os.path.join(self.tmp, "ziel_" + fmt)
                self.import_path(exp, out=target)
                n = {key: len(features(*key, "Feldgrenzen")) for key in (A, B)}
                self.import_path(exp, out=target, answer=YES)
                self.assertEqual(len(self.questions), 1, fmt)
                for key in (A, B):
                    self.assertEqual(len(features(*key, "Feldgrenzen")), n[key], (fmt, key))
                    self.assertTrue(all(c == 1 for c in self.layer_counts(key).values()), (fmt, self.layer_counts(key)))
