# -*- coding: utf-8 -*-
"""
Führt alle Tests des Plugins ohne Oberfläche aus.

Aufruf mit dem Python einer QGIS-Installation, z. B.:
    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" tests\\run_tests.py
    "C:\\Program Files\\QGIS 4.2.3\\bin\\python-qgis.bat"     tests\\run_tests.py
Für beide Versionen nacheinander: tests\\run_tests.bat

Optional: Namen einzelner Testdateien/-klassen anhängen, z. B. "test_tracks".
Exit-Code 0 = alles bestanden.
"""

import os
import sys
import unittest
import warnings

# Hinweise auf veraltete QGIS-APIs u. ä. nicht in die Testausgabe mischen
warnings.simplefilter("ignore", DeprecationWarning)
warnings.simplefilter("ignore", ResourceWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# QGIS-Python und Processing-Plugin auffindbar machen
prefix = os.environ.get("QGIS_PREFIX_PATH", "")
if prefix:
    for p in (os.path.join(prefix, "python"), os.path.join(prefix, "python", "plugins")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
# Plugin als Paket importierbar machen (Ordner darüber)
sys.path.insert(0, os.path.dirname(PLUGIN_DIR))
sys.path.insert(0, HERE)

from qgis.core import Qgis, QgsApplication  # noqa: E402

app = QgsApplication([], False)
app.initQgis()
try:
    # GDAL-Hinweise (z. B. Shapefile-Feldbreite) nicht in die Testausgabe mischen
    from osgeo import gdal  # noqa: E402
    gdal.PushErrorHandler("CPLQuietErrorHandler")
except Exception:  # nosec B110
    pass
try:
    from processing.core.Processing import Processing  # noqa: E402
    Processing.initialize()
except Exception:  # nosec B110 - Processing ist für die Tests optional
    pass


def main():
    from qgis.PyQt.QtCore import QT_VERSION_STR
    print(f"QGIS {Qgis.version()} | Qt {QT_VERSION_STR} | Plugin {PLUGIN_DIR}")

    # Spracheinstellung sichern und am Ende wiederherstellen
    import support
    lang_before = support.translations.get_language()

    loader = unittest.TestLoader()
    names = [a for a in sys.argv[1:] if not a.startswith("-")]
    if names:
        suite = unittest.TestSuite(loader.loadTestsFromName(n) for n in names)
    else:
        suite = loader.discover(HERE, pattern="test_*.py", top_level_dir=HERE)
    verbosity = 2 if "-v" in sys.argv else 1
    result = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stdout, warnings="ignore").run(suite)

    support.translations.set_language(lang_before)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    code = main()
    app.exitQgis()
    sys.exit(code)
