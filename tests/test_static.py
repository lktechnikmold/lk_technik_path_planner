# -*- coding: utf-8 -*-
"""
Prüfungen am Quelltext: Übersetzungen vollständig, Qt5/Qt6-Verträglichkeit,
metadata.txt.
"""

import ast
import configparser
import glob
import os
import re
import string
import unittest

from qgis.PyQt import QtCore, QtGui, QtWidgets

from support import PLUGIN_DIR, read_text, translations

SOURCES = [p for p in glob.glob(os.path.join(PLUGIN_DIR, "*.py"))
           if os.path.basename(p) not in ("resources.py",)]

# Texte, die in allen Sprachen gleich bleiben
LANGUAGE_NEUTRAL = {"LK-Technik Path Planner", "&LK-Technik Path Planner", "Terminal:", "…"}


def _ui_strings():
    """Alle Literale aus _tr("…") / tr("…") / _mk_text(widget, "…")."""
    out = {}
    for path in SOURCES:
        if os.path.basename(path) == "translations.py":
            continue
        tree = ast.parse(read_text(path))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            arg = None
            if name in ("_tr", "tr") and n.args and isinstance(n.args[0], ast.Constant):
                arg = n.args[0].value
            elif name == "_mk_text" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant):
                arg = n.args[1].value
            if isinstance(arg, str):
                out.setdefault(arg, f"{os.path.basename(path)}:{n.lineno}")
    return out


def _placeholders(text):
    return sorted(f for _, f, _, _ in string.Formatter().parse(text) if f)


class TestTranslations(unittest.TestCase):

    def test_every_ui_text_has_english(self):
        en = translations.TRANSLATIONS["en"]
        missing = {k: v for k, v in _ui_strings().items() if k not in en and k not in LANGUAGE_NEUTRAL}
        self.assertEqual(missing, {}, "Texte ohne englische Übersetzung")

    def test_placeholders_match(self):
        bad = {k: v for k, v in translations.TRANSLATIONS["en"].items() if _placeholders(k) != _placeholders(v)}
        self.assertEqual(bad, {}, "Platzhalter {…} stimmen nicht überein")


class TestQtCompatibility(unittest.TestCase):
    """Das Plugin muss mit PyQt5 (QGIS 3) und PyQt6 (QGIS 4) laufen."""

    def _lines(self):
        for path in SOURCES:
            for ln, line in enumerate(read_text(path).splitlines(), 1):
                yield os.path.basename(path), ln, line.split("#")[0]

    def test_no_exec_underscore(self):
        hits = [f"{p}:{ln}" for p, ln, code in self._lines() if re.search(r"\.exec_\(", code)]
        self.assertEqual(hits, [], "exec_() gibt es in PyQt6 nicht - exec() verwenden")

    def test_no_direct_pyqt_import(self):
        files = SOURCES + [os.path.join(PLUGIN_DIR, "resources.py")]
        hits = [f"{os.path.basename(p)}:{ln}" for p in files
                for ln, line in enumerate(read_text(p).splitlines(), 1)
                if re.match(r"\s*(from|import)\s+PyQt[56]\b", line)]
        self.assertEqual(hits, [], "immer über qgis.PyQt importieren")

    def test_scoped_enums(self):
        """Qt-Enums nur in der Langform (Qt.ItemDataRole.UserRole statt Qt.UserRole)."""
        ns = {}
        for m in (QtCore, QtGui, QtWidgets):
            for k in dir(m):
                if k.startswith("Q") and not k.startswith(("Qgs", "Qgis")):
                    ns[k] = getattr(m, k)
        ns["Qt"] = QtCore.Qt
        allowed = {"QVariant"}            # QVariant.Int usw. ergänzt QGIS 4 selbst
        pat = re.compile(r"\b(Qt|Q[A-Z][A-Za-z]+)\.([A-Z]\w*)\b(?!\s*[.(])")
        hits = []
        for p, ln, code in self._lines():
            for m in pat.finditer(code):
                cls, attr = m.groups()
                if cls in allowed or cls not in ns:
                    continue
                obj = getattr(ns[cls], attr, None)
                if obj is None or not isinstance(obj, type):
                    hits.append(f"{p}:{ln} {cls}.{attr}")
        self.assertEqual(hits, [], "Kurzform von Qt-Enums")


class TestMetadata(unittest.TestCase):

    def test_qgis_versions(self):
        cp = configparser.ConfigParser()
        cp.read(os.path.join(PLUGIN_DIR, "metadata.txt"), encoding="utf-8")
        g = cp["general"]
        self.assertEqual(g.get("supportsQt6", "").lower(), "true")
        self.assertGreaterEqual(float(g["qgisMaximumVersion"]), 4.0)
        self.assertLess(float(g["qgisMinimumVersion"]), 4.0)
        self.assertTrue(re.match(r"^\d+\.\d+\.\d+$", g["version"]), g["version"])

    def test_sources_compile(self):
        for p in SOURCES:
            compile(read_text(p), p, "exec")
