# -*- coding: utf-8 -*-
"""Dialog: Terminals, Reiter, Sprache, Wartemeldung, Layerstile."""

import os

from qgis.core import QgsVectorLayer
from qgis.PyQt.QtWidgets import QApplication, QProgressDialog

from support import PLUGIN_DIR, PluginTestCase, groups, layer, mod, translations

A = ("Testkunde A", "Testbetrieb A")


class TestTerminals(PluginTestCase):

    def test_default_terminal(self):
        self.assertEqual(mod.DEFAULT_TERMINAL, ("ISOXML", "v4", "4.2"))
        self.assertEqual(self.dlg.selected_terminal(), ("ISOXML", "v4", "4.2"))

    def test_all_terminals_listed(self):
        cmb = self.dlg.cmb_terminal
        listed = {tuple(cmb.itemData(i)) for i in range(cmb.count()) if cmb.itemData(i)}
        self.assertEqual(listed, {tuple(t) for t in mod.TERMINALS})
        self.assertEqual(len(mod.TERMINALS), len(set(mod.TERMINALS)), "keine doppelten Terminals")

    def test_formats(self):
        expected = {("CN1", "CNH"): "CN1", ("Case/Steyr", "AFS Pro 700"): "CN1",
                    ("New Holland", "Intelli View 4"): "CN1", ("New Holland", "Intelli View 12"): "4.2",
                    ("John Deere", "GS4 / GS5"): "Gen4", ("Trimble", "CFX / FM / FMX / GFX / TMX"): "AgGPS", ("Claas", "S10"): "3.3",
                    ("Case/Steyr", "Fleet Pro"): "4.2", ("Topcon", "X / XD"): "4.2", ("LACOS", "LC:ONE"): "4.2"}
        for (brand, model), fmt in expected.items():
            self.assertEqual(self.select_terminal(brand, model)[2], fmt, (brand, model))
        self.assertNotIn(("Case/Steyr", "S-Fleet Pro"), [(t[0], t[1]) for t in mod.TERMINALS])
        # zusammengefasste Terminals: je Marke/Format genau ein Eintrag
        merged = {("Trimble", "AgGPS"): "CFX / FM / FMX / GFX / TMX", ("Raven", "4.2"): "CR / Viper",
                  ("Topcon", "4.2"): "X / XD", ("John Deere", "Gen4"): "GS4 / GS5",
                  ("New Holland", "AgGPS"): "FM 750 / FM 1000", ("Case/Steyr", "AgGPS"): "FM 750 / FM 1000",
                  ("Deutz", "4.2"): "iMonitor"}
        for (brand, fmt), model in merged.items():
            self.assertEqual([t[1] for t in mod.TERMINALS if t[0] == brand and t[2] == fmt], [model], brand)
        for fmt in {t[2] for t in mod.TERMINALS}:
            self.assertNotEqual(mod._format_label(fmt), "", fmt)

    def test_format_label_and_cn1_hint(self):
        for brand, model, fmt in mod.TERMINALS:
            self.select_terminal(brand, model)
            self.assertIn(mod._format_label(fmt), self.dlg.lbl_format.text())
            self.assertEqual(not self.dlg.lbl_cn1_hint.isHidden(), fmt == "CN1", (brand, model))


class TestDialog(PluginTestCase):

    def test_mode_pages(self):
        for radio, page in ((self.dlg.mode_export, self.dlg.page_export),
                            (self.dlg.mode_import, self.dlg.page_import),
                            (self.dlg.mode_tracks, self.dlg.page_tracks)):
            radio.setChecked(True)
            self.dlg._sync_mode()
            self.assertIs(self.dlg.stack.currentWidget(), page)

    def test_advanced_export_options_toggle(self):
        self.assertTrue(self.dlg.adv_export_widget.isHidden())
        self.dlg.btn_adv_export.setChecked(True)
        self.assertFalse(self.dlg.adv_export_widget.isHidden())
        self.assertEqual(self.dlg.spin_densify_interval.value(), 3.0)
        self.assertEqual(self.dlg.spin_extend_curves.value(), 15.0)
        self.assertFalse(self.dlg.spin_densify_interval.isEnabled())
        self.dlg.chk_densify_curves.setChecked(True)
        self.assertTrue(self.dlg.spin_densify_interval.isEnabled())

    def test_track_tab_widgets(self):
        self.assertEqual(self.dlg.spin_track_offset.value(), 0.0)
        self.assertEqual(self.dlg.txt_track_prefix.maxLength(), 20)
        self.assertFalse(self.dlg.chk_track_segment.isChecked())

    def test_language_switch(self):
        self.import_test_data()
        idx = self.dlg.cmb_lang.findData("en")
        self.dlg.cmb_lang.setCurrentIndex(idx)
        self.plugin._on_language_changed("en")
        try:
            self.assertEqual(translations.get_language(), "en")
            self.assertEqual(self.dlg.run_button.text(), "Run")
            self.assertEqual(mod._tr("Gerade {n}").format(n=1), "Straight 1")
            # Layer werden umbenannt, aber weiterhin gefunden
            grp = groups()[A]
            names = {ch.layer().name() for ch in grp.children() if hasattr(ch, "layer") and ch.layer()}
            self.assertIn("Boundaries", names)
            self.assertIsNotNone(layer(*A, "Feldgrenzen"))
        finally:
            self.dlg.cmb_lang.setCurrentIndex(self.dlg.cmb_lang.findData("de"))
            self.plugin._on_language_changed("de")
        self.assertEqual(self.dlg.run_button.text(), "Ausführen")
        self.assertIn("Feldgrenzen", {ch.layer().name() for ch in groups()[A].children()
                                      if hasattr(ch, "layer") and ch.layer()})


class TestWaitMessage(PluginTestCase):

    def _visible(self):
        return [w.labelText() for w in QApplication.topLevelWidgets()
                if isinstance(w, QProgressDialog) and w.isVisible()]

    def test_shown_during_work_and_removed(self):
        seen = {}

        def work():
            seen["text"] = self._visible()
            seen["cursor"] = QApplication.overrideCursor() is not None
            return 42

        self.assertEqual(self.plugin._run_with_wait_message("Felddaten werden importiert, bitte warten…", work), 42)
        self.assertEqual(seen["text"], ["Felddaten werden importiert, bitte warten…"])
        self.assertTrue(seen["cursor"])
        self.assertEqual(self._visible(), [])
        self.assertIsNone(QApplication.overrideCursor())

    def test_removed_after_error(self):
        def boom():
            raise RuntimeError("x")

        with self.assertRaises(RuntimeError):
            self.plugin._run_with_wait_message("Test", boom)
        self.assertEqual(self._visible(), [])
        self.assertIsNone(QApplication.overrideCursor())


class TestStyles(PluginTestCase):

    def _styled(self, name, uri):
        lyr = QgsVectorLayer(uri, name, "memory")
        self.plugin._apply_predefined_style(lyr)
        return lyr

    def test_label_buffers(self):
        for name, uri in (("Fahrspuren", "LineString?field=ID:integer&field=Name:string&field=Segment:string"),
                          ("Feldgrenzen", "Polygon?field=ID:integer&field=Name:string&field=Flaeche:double")):
            lyr = self._styled(name, uri)
            buf = lyr.labeling().settings().format().buffer()
            self.assertTrue(lyr.labelsEnabled(), name)
            self.assertTrue(buf.enabled(), name)
            self.assertEqual(buf.color().name(), "#ffffff", name)

    def test_constraints(self):
        tracks = self._styled("Fahrspuren", "LineString?field=ID:integer&field=Name:string&field=Segment:string")
        self.assertIn("IS NULL", tracks.constraintExpression(tracks.fields().indexOf("Segment")),
                      "Segment bleibt optional")
        points = self._styled("Punkthindernis", "Point?field=ID:integer&field=Name:string&field=befahrbar:integer")
        self.assertNotIn("IS NULL", points.constraintExpression(points.fields().indexOf("Name")),
                         "Punkthindernis braucht einen Namen")
        areas = self._styled("Flaechenhindernis",
                             "MultiPolygon?field=ID:integer&field=Name:string&field=befahrbar:integer")
        self.assertIn("IS NULL", areas.constraintExpression(areas.fields().indexOf("Name")),
                      "Name des Flächenhindernisses ist optional")

    def test_all_style_files_load(self):
        geom = {"Fahrspuren.qml": "LineString", "Feldgrenzen.qml": "Polygon", "Flaechenhindernis.qml": "MultiPolygon",
                "Punkthindernis.qml": "Point"}
        self.assertEqual(sorted(os.listdir(os.path.join(PLUGIN_DIR, "styles"))), sorted(geom))
        for fn, gtype in geom.items():
            lyr = QgsVectorLayer(f"{gtype}?field=ID:integer&field=Name:string", fn, "memory")
            msg, ok = lyr.loadNamedStyle(os.path.join(PLUGIN_DIR, "styles", fn))
            self.assertTrue(ok, f"{fn}: {msg}")
