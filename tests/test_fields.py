# -*- coding: utf-8 -*-
"""Feldverwaltung: Kunde/Betrieb/Feld anlegen, umbenennen, löschen, Katalog, Zoom, Kontextmenü."""

import os
from unittest.mock import patch

from qgis.core import QgsFeature, QgsVectorFileWriter, QgsVectorLayer

from support import (LONG_NAME, PluginTestCase, QgsGeometry, QgsPointXY, QMessageBox, answer_dialogs, catalog,
                     features, groups, layer, ll, mod, read_text)

A = ("Testkunde A", "Testbetrieb A")


def field_items(tree):
    out = {}
    for i in range(tree.topLevelItemCount()):
        ctr = tree.topLevelItem(i)
        for j in range(ctr.childCount()):
            frm = ctr.child(j)
            for k in range(frm.childCount()):
                out[(ctr.text(0), frm.text(0), frm.child(k).text(0))] = frm.child(k)
    return out


class TestCreate(PluginTestCase):

    def test_add_customer(self):
        with answer_dialogs(text="  Neuer   Kunde "):
            self.plugin._ui_add_customer()
        self.assertIn("Neuer Kunde", [g.name() for g in mod.QgsProject.instance().layerTreeRoot().children()])
        with answer_dialogs(text=None):
            self.plugin._ui_add_customer()
        self.assertEqual(len(mod.QgsProject.instance().layerTreeRoot().children()), 1)

    def test_add_farm_requires_customer(self):
        with answer_dialogs(farm=("X", "Y")) as seen:
            self.plugin._ui_add_farm()
        self.assertEqual(len(seen["information"]), 1)
        self.assertEqual(groups(), {})

    def test_add_farm_creates_layers_and_catalog(self):
        with answer_dialogs(text="Kunde Neu"):
            self.plugin._ui_add_customer()
        with answer_dialogs(farm=("Kunde Neu", "Betrieb Neu")):
            self.plugin._ui_add_farm()
        self.assertIn(("Kunde Neu", "Betrieb Neu"), groups())
        folder = os.path.join(self.tmp, "Kunde Neu", "Betrieb Neu")
        for n in ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"):
            self.assertTrue(os.path.isfile(os.path.join(folder, n + ".gpkg")), n)
            self.assertIsNotNone(layer("Kunde Neu", "Betrieb Neu", n), n)
        self.assertTrue(os.path.isfile(os.path.join(folder, "Felder.csv")))
        names = [f.name() for f in layer("Kunde Neu", "Betrieb Neu", "Flaechenhindernis").fields()]
        self.assertEqual([n for n in names if n != "fid"], ["ID", "Name", "befahrbar"])

    def test_add_field_without_boundary(self):
        self.import_test_data()
        with answer_dialogs(field=(A, "Hausacker")):
            self.plugin._ui_add_field()
        self.assertEqual(catalog(*A)[4], "Hausacker")
        with answer_dialogs(field=(A, "")):
            self.plugin._ui_add_field()
        self.assertEqual(catalog(*A)[5], "Feld 5")
        self.assertIn("Hausacker", [k[2] for k in field_items(self.dlg.tree)])


class TestEdit(PluginTestCase):

    def setUp(self):
        super().setUp()
        self.import_test_data()

    def test_rename(self):
        with answer_dialogs(text="Umbenannt"):
            self.plugin._rename_field(*A, 1, "Rechteck mit Loch")
        self.assertEqual(catalog(*A)[1], "Umbenannt")
        # Name der Feldgrenze bleibt eigenständig
        self.assertEqual(features(*A, "Feldgrenzen", 1)[0]["Name"], "Rechteck mit Loch")
        with answer_dialogs(text=None):
            self.plugin._rename_field(*A, 1, "Umbenannt")
        self.assertEqual(catalog(*A)[1], "Umbenannt")

    def test_delete_cancel_and_confirm(self):
        with answer_dialogs(message_box=QMessageBox.StandardButton.No) as seen:
            self.plugin._delete_field(*A, 1, "Rechteck mit Loch")
        self.assertEqual(len(seen["message_box"]), 1)
        self.assertEqual(len(features(*A, "Fahrspuren", 1)), 4)
        with answer_dialogs(message_box=QMessageBox.StandardButton.Yes):
            self.plugin._delete_field(*A, 1, "Rechteck mit Loch")
        for n in ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"):
            self.assertEqual(features(*A, n, 1), [], n)
        self.assertNotIn(1, catalog(*A))
        self.assertIn(2, catalog(*A))

    def test_delete_several(self):
        with answer_dialogs(message_box=QMessageBox.StandardButton.Yes) as seen:
            self.plugin._delete_fields([(*A, 1, "Rechteck mit Loch"), (*A, 2, "Zwei Teile")])
        self.assertIn("2 Felder", seen["message_box"][0])
        self.assertEqual(catalog(*A), {3: LONG_NAME})

    def test_new_boundary_is_added_to_catalog(self):
        lyr = layer(*A, "Feldgrenzen")
        f = QgsFeature(lyr.fields())
        f.setAttribute("ID", 9)
        f.setAttribute("Name", "Neue Grenze")
        f.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(*reversed(ll(x, y)))
                                                   for x, y in ((0, 600), (50, 600), (50, 650), (0, 600))]]))
        lyr.dataProvider().addFeatures([f])
        self.plugin._sync_all_felder_catalogs()
        self.assertEqual(catalog(*A).get(9), "Neue Grenze")
        csv = [p for p in os.listdir(os.path.dirname(lyr.source().split("|")[0])) if p.lower() == "felder.csv"][0]
        self.assertIn("Neue Grenze", read_text(os.path.join(os.path.dirname(lyr.source().split("|")[0]), csv)))


class TestZoomAndMenu(PluginTestCase):

    def setUp(self):
        super().setUp()
        self.import_test_data()
        self.dlg.refresh_tree()

    def test_zoom_covers_all_boundaries(self):
        self.plugin._zoom_to_field(*A, 2, "Zwei Teile")
        ext = self.canvas.extent()
        tr = mod.QgsCoordinateTransform(layer(*A, "Feldgrenzen").crs(), self.canvas.mapSettings().destinationCrs(),
                                        mod.QgsProject.instance())
        for f in features(*A, "Feldgrenzen", 2):
            self.assertTrue(ext.contains(tr.transformBoundingBox(f.geometry().boundingBox())))

    def test_zoom_without_geometry(self):
        with answer_dialogs(field=(A, "Leer")):
            self.plugin._ui_add_field()
        self.plugin._zoom_to_field(*A, 4, "Leer")
        self.assertIn("keine Geometrie", self.last_msg())

    def _menu(self, items):
        tree = self.dlg.tree
        tree.clearSelection()
        for it in items:
            it.setSelected(True)
        captured = []
        with patch.object(mod.QMenu, "exec", lambda m, *a: captured.append([x.text() for x in m.actions() if x.text()])):
            self.plugin._on_tree_context_menu(tree.visualItemRect(items[0]).center())
        return captured[-1] if captured else None

    def test_context_menu_single_and_multi(self):
        items = field_items(self.dlg.tree)
        one = items[(*A, "Rechteck mit Loch")]
        two = items[(*A, "Zwei Teile")]
        self.assertEqual(self._menu([one]), ["Auf Feld zoomen", "Feld umbenennen…", "Feld löschen…"])
        self.assertEqual(self._menu([one, two]), ["Felder löschen…"])
        ctr = self.dlg.tree.topLevelItem(0)
        self.assertIsNone(self._menu([ctr]))

    def test_export_tree_multiselect(self):
        self.assertEqual(self.dlg.tree.selectionMode(), mod.QTreeWidget.SelectionMode.ExtendedSelection)


class TestAreaObstacleNameField(PluginTestCase):

    def test_old_layer_gets_name_field_and_style(self):
        gp = os.path.join(self.tmp, "Flaechenhindernis.gpkg")
        mem = QgsVectorLayer("MultiPolygon?crs=EPSG:4326&field=ID:integer&field=befahrbar:integer",
                             "Flaechenhindernis", "memory")
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        QgsVectorFileWriter.writeAsVectorFormatV3(mem, gp, mod.QgsProject.instance().transformContext(), opts)
        old = QgsVectorLayer(gp, "Flaechenhindernis", "ogr")
        self.plugin._ensure_area_name_field(old)
        self.plugin._apply_predefined_style(old)
        self.assertIn("Name", old.fields().names())
        self.assertIn("Name", QgsVectorLayer(gp, "x", "ogr").fields().names(), "in der Datei gespeichert")
        idx = old.fields().indexOf("Name")
        self.assertIn("length", old.constraintExpression(idx))
        cols = [c.name for c in old.attributeTableConfig().columns() if not c.hidden and c.name]
        self.assertEqual(cols, ["ID", "Name", "befahrbar"])
        self.assertEqual([e.name() for e in old.editFormConfig().tabs()], ["ID", "Name", "befahrbar"])

