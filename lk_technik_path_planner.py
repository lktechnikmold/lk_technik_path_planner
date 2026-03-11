# -*- coding: utf-8 -*-
"""
LK-Technik Path Planner – combined QGIS Plugin (Import & Export)

Dieses Plugin vereint Import und Export von ISOXML-Daten:
- Import (ISOXML → QGIS)
- Export (QGIS → ISOXML)

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
Version: 1.0.0
Date: 2025-11-04
"""


import os, os.path, math, xml.etree.ElementTree as ET, xml.dom.minidom

import os, os.path, math, xml.etree.ElementTree as ET, xml.dom.minidom

from qgis.PyQt.QtCore import Qt, QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon, QPixmap
try:
    from . import resources
except Exception:
    import resources
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QFileDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLineEdit, QLabel, QGroupBox, QCheckBox, QRadioButton, QStackedWidget,
    QFormLayout
)

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsField, QgsFields, QgsFeature, QgsGeometry, QgsPointXY,
    QgsLayerTreeGroup, QgsCoordinateReferenceSystem, QgsCoordinateTransform
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

def _field_map(layer: QgsVectorLayer) -> dict:
    """maps lowercase fieldname -> actual fieldname"""
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

class ToolboxDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LK-Technik Path Planner")
        self.setWindowIcon(QIcon(":/isoxml/icons/logo.png"))
        self.setMinimumWidth(720)

        self.mode_import = QRadioButton("Import (ISOXML → QGIS)")
        self.mode_export = QRadioButton("Export (QGIS → ISOXML)")
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
                self, "Zielordner für ISOXML wählen"
            )
            if dn:
                self.out_line.setText(dn)

        btn.clicked.connect(_pick_file)
        path_row.addWidget(QLabel("TASKDATA.XML:"))
        path_row.addWidget(self.out_line, 1)
        path_row.addWidget(btn)
        v.addLayout(path_row)

        # v3/segments options
        opt_row = QHBoxLayout()
        self.chk_v3 = QCheckBox("ISOXML v3 (sonst v4)")
        self.chk_seg = QCheckBox("Kontursegmente (nur v4)")
        def _toggle_v3():
            self.chk_seg.setEnabled(not self.chk_v3.isChecked())
            if self.chk_v3.isChecked():
                self.chk_seg.setChecked(False)
        self.chk_v3.toggled.connect(_toggle_v3)
        _toggle_v3()
        opt_row.addWidget(self.chk_v3)
        opt_row.addWidget(self.chk_seg)
        opt_row.addStretch(1)
        v.addLayout(opt_row)

        # CTR→FRM→Felder tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Kunde / Betrieb / Feld (Feldgrenzen)"])
        self.tree.setColumnCount(1)
        self.tree.setSelectionMode(QTreeWidget.NoSelection)
        v.addWidget(QLabel("Wähle, was exportiert werden soll:"))
        v.addWidget(self.tree, 1)

        return w

    def _build_import_page(self):
        w = QGroupBox("Import-Optionen")
        lay = QFormLayout(w)

        self.in_line = QLineEdit()
        btn_in = QPushButton("…")
        def _pick_in():
            fn, _ = QFileDialog.getOpenFileName(self, "ISOXML wählen", '', 'XML (*.xml);;Alle Dateien (*)')
            if fn:
                self.in_line.setText(fn)
        btn_in.clicked.connect(_pick_in)
        h1 = QHBoxLayout(); h1.addWidget(self.in_line, 1); h1.addWidget(btn_in)
        lay.addRow(QLabel("TASKDATA.XML:"), h1)

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
                # find Feldgrenzen
                poly_layer = None
                for child in frm_node.children():
                    try:
                        lyr = child.layer()
                    except Exception:
                        lyr = None
                    if isinstance(lyr, QgsVectorLayer) and lyr.name() == "Feldgrenzen":
                        poly_layer = lyr
                        break
                if poly_layer is None:
                    continue
                fmap = _field_map(poly_layer)
                name_field = _pick_field(fmap, "Name")
                id_field   = _pick_field(fmap, "ID")

                for feat in poly_layer.getFeatures():
                    label = str(feat[name_field]) if name_field else str(feat.id())
                    item = QTreeWidgetItem([label])

                    stored_id = feat[id_field] if id_field else feat.id()
                    try:
                        stored_id = int(stored_id)
                    except Exception:
                        stored_id = int(feat.id())

                    item.setData(0, Qt.UserRole, stored_id)
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

class LkTechnikPathPlanner:
    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.menu = _tr('&LK-Technik Path Planner')
        self.first_start = True

    def tr(self, m):
        return _tr(m)

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

    def unload(self):
        for a in self.actions:
            self.iface.removeToolBarIcon(a)
            self.iface.removePluginMenu(self.menu, a)

    def run(self):
        if self.first_start:
            self.first_start = False
            self.dlg = ToolboxDialog(self.iface.mainWindow())
            self.dlg.run_button.clicked.connect(self._on_run)
        self.dlg.refresh_tree()
        self.dlg.show()
        self.dlg.exec_()

    def _on_run(self):
        if self.dlg.mode_export.isChecked():
            self._do_export()
        else:
            self._do_import()

    # ------------------------- IMPORT -------------------------
    def _do_import(self):
        path = self.dlg.in_line.text().strip()
        out_dir = self.dlg.out_dir_line.text().strip() or None
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

        def _find_or_create_group(root_group, name: str) -> QgsLayerTreeGroup:
            for ch in root_group.children():
                if isinstance(ch, QgsLayerTreeGroup) and ch.name() == name:
                    return ch
            return root_group.addGroup(name)

        def _ensure_hierarchy(ctr_name: str, frm_name: str) -> QgsLayerTreeGroup:
            root_g = project.layerTreeRoot()
            ctr_grp = _find_or_create_group(root_g, ctr_name)
            frm_grp = _find_or_create_group(ctr_grp, frm_name)
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

            return {"Feldgrenzen": field_layer, "Fahrspuren": line_layer, "Punkthindernis": point_layer, "Flaechenhindernis": area_layer}

        def _persist_frm_layers(layers_dict, ctr_name: str, frm_name: str):
            if not out_dir:
                return layers_dict
            base = os.path.join(out_dir, _safe(ctr_name), _safe(frm_name))
            os.makedirs(base, exist_ok=True)
            new_layers = {}
            from qgis.core import QgsVectorFileWriter
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
                    new_layers[key] = file_layer
                else:
                    new_layers[key] = mem_layer
            return new_layers

        def _ensure_frm(frm_id: str, ctr_name_hint: str = None):
            if frm_id in per_frm_layers:
                return per_frm_layers[frm_id], per_frm_groups[frm_id]
            info = frm_map.get(frm_id, {"name": frm_id, "ctr": None})
            ctr_name = ctr_map.get(info.get("ctr"), info.get("ctr") or ctr_name_hint or "Unbekannter_Kunde")
            frm_name = info.get("name") or frm_id
            frm_group = _ensure_hierarchy(ctr_name, frm_name)
            layers = _create_frm_layers()
            for lyr in layers.values():
                project.addMapLayer(lyr, False)
                frm_group.addLayer(lyr)
            layers = _persist_frm_layers(layers, ctr_name, frm_name)
            per_frm_layers[frm_id] = layers
            per_frm_groups[frm_id] = frm_group
            return layers, frm_group

        # PFDs
        for pfd in root.findall('.//PFD'):
            pfd_id = pfd.get("A") or pfd.get("PFDId") or "PFD0"
            pfd_name = pfd.get("C") or pfd.get("B") or ""
            pfd_area = pfd.get("D", "0")
            frm_ref = pfd.get("F") or pfd.get("FRMIdRef") or pfd.get("C")
            try:
                numeric_id = int(''.join(ch for ch in pfd_id if ch.isdigit()) or 0)
            except Exception:
                numeric_id = 0
            try:
                area_val = int(pfd_area)
            except Exception:
                area_val = 0.0

            ctr_ref_from_pfd = pfd.get("E") or pfd.get("CTRIdRef")
            ctr_name_hint = ctr_map.get(ctr_ref_from_pfd, ctr_ref_from_pfd or "Unbekannter_Kunde")
            frm_layers, _grp = _ensure_frm(frm_ref or "FRM", ctr_name_hint)
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
                    for pnt in lsg_field.findall("PNT"):
                        a_val = pnt.get("A")
                        if a_val in ("10", "2"):
                            lat = float(pnt.get("C", "0")); lon = float(pnt.get("D", "0"))
                            ring_pts.append(_tx_pt_xy(lon, lat))
                    if len(ring_pts) > 2:
                        feat_f = QgsFeature(field_layer.fields())
                        feat_f.setAttribute("ID", numeric_id); feat_f.setAttribute("Name", pfd_name); feat_f.setAttribute("Flaeche", area_val)
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

        self.iface.messageBar().pushMessage("Success", "ISOXML importiert (CTR → FRM → Layer).", level=Qgis.Success, duration=4)
        self.dlg.accept()

    # ------------------------- EXPORT -------------------------
    def _do_export(self):
        def _has_memory_layers():
            root = QgsProject.instance().layerTreeRoot()
            for node in root.findLayers():
                lyr = node.layer()
                if isinstance(lyr, QgsVectorLayer):
                    if lyr.providerType() == "memory":
                        return True
            return False
            
        if _has_memory_layers():
            self.iface.messageBar().pushMessage(
                "Export nicht möglich",
                "Es sind nicht gespeicherte Layer (Temporärlayer) im Projekt.\n"
                "Bitte zuerst beim Import einen Ausgabeordner wählen\n"
                "oder die Layer manuell speichern.",
                level=Qgis.Warning,
                duration=8
            )
            return


        out_dir = self.dlg.out_line.text().strip()
        if not out_dir:
            self.iface.messageBar().pushMessage(
                "Fehler", "Bitte Zielordner wählen.",
                level=Qgis.Warning, duration=4
            )
            return

        output_file_path = os.path.join(out_dir, "TASKDATA.XML")

        is_v3 = self.dlg.chk_v3.isChecked()
        use_segments = (self.dlg.chk_seg.isChecked() and not is_v3)

        selected = self.dlg.selected_export_map()
        if not selected:
            self.iface.messageBar().pushMessage("Hinweis", "Keine Auswahl getroffen.", level=Qgis.Info, duration=4)
            return

        root_xml = ET.Element('ISO11783_TaskData', {
            "VersionMajor": "3" if is_v3 else "4",
            "VersionMinor": "0",
            "ManagementSoftwareManufacturer": "LK-Technik Mold",
            "ManagementSoftwareVersion": "2020.02.00.294",
            "DataTransferOrigin": "1"
        })

        ctr_idx = 1; frm_idx = 1; pnt_global = 1
        ggp_global = 1
        gpn_global = 1

        project = QgsProject.instance()
        
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
            ctr_id = f"CTR{ctr_idx}"; ctr_idx += 1
            ET.SubElement(root_xml, 'CTR', {'A': ctr_id, 'B': ctr_name})

            for frm_group in _iter_frm_groups(ctr_group):
                frm_name = frm_group.name()
                if frm_name not in selected[ctr_name]:
                    continue
                frm_id = f"FRM{frm_idx}"; frm_idx += 1
                ET.SubElement(root_xml, 'FRM', {'A': frm_id, 'B': frm_name, 'I': ctr_id})

                polygon_layer = _find_child_layer_by_name(frm_group, "Feldgrenzen")
                line_layer = _find_child_layer_by_name(frm_group, "Fahrspuren")
                point_layer = _find_child_layer_by_name(frm_group, "Punkthindernis")
                fh_layer = _find_child_layer_by_name(frm_group, "Flaechenhindernis")

                if not polygon_layer:
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

                field_ids_filter = selected[ctr_name][frm_name]

                poly_fmap = _field_map(polygon_layer)
                id_field   = _pick_field(poly_fmap, "ID")
                name_field = _pick_field(poly_fmap, "Name")
                area_field = _pick_field(poly_fmap, "Flaeche")

                for field_feature in polygon_layer.getFeatures():
                    raw_id = field_feature[id_field] if id_field else field_feature.id()
                    try:
                        field_id = int(raw_id)
                    except Exception:
                        field_id = int(field_feature.id())

                    if (field_ids_filter is not None) and (field_id not in field_ids_filter):
                        continue

                    field_name = field_feature[name_field] if name_field else str(field_feature.id())
                    field_area = field_feature[area_field] if area_field else 0

                    pfd_element = ET.SubElement(root_xml, 'PFD', {
                        'A': f'PFD{field_id}', 'C': str(field_name), 'D': str(int(field_area)), 'E': ctr_id, 'F': frm_id
                    })

                    #Boundary
                    pln_element = ET.SubElement(pfd_element, 'PLN', {
                        'A': '1', 'B': str(field_name), 'C': str(int(field_area)), 'E': f'PLN{field_id}'
                    })
                    lsg_field = ET.SubElement(pln_element, 'LSG', {'A': '1'})

                    geom = field_feature.geometry()
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
                                lines = track_feature.geometry().asMultiPolyline() or []
                                if not lines:
                                    single = track_feature.geometry().asPolyline()
                                    if single:
                                        lines = [single]
                                track_name = track_feature['Name'] if 'Name' in line_names else ''
                                for line in lines:
                                    lsg_line = ET.SubElement(pfd_element, 'LSG', {'A': '5', 'B': track_name})
                                    for i, pt in enumerate(line):
                                        a_val = '6' if i == 0 else ('7' if i == len(line)-1 else '9')
                                        lonl, latl = _to_wgs_xy_from_point(pt, ct_line)
                                        ET.SubElement(lsg_line, 'PNT', {'A': a_val, 'C': str(latl), 'D': str(lonl)})
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
                                        lines = track_feature.geometry().asMultiPolyline() or []
                                        if not lines:
                                            single = track_feature.geometry().asPolyline()
                                            if single:
                                                lines = [single]
                                        for line in lines:
                                            c_value = '3' if len(line) > 2 else '1'
                                            gpn_element = ET.SubElement(ggp_element, 'GPN', {
                                                'A': next_gpn_id(),
                                                'B': track_feature['Name'],
                                                'C': c_value
                                            })

                                            inner_lsg = ET.SubElement(gpn_element, 'LSG', {'A': '5'})
                                            for i, pt in enumerate(line):
                                                a_val = '6' if i == 0 else ('7' if i == len(line)-1 else '9')
                                                lonl, latl = _to_wgs_xy_from_point(pt, ct_line)
                                                ET.SubElement(inner_lsg, 'PNT', {'A': a_val, 'C': str(latl), 'D': str(lonl)})
                                for track_feature in non_segment:
                                    lines = track_feature.geometry().asMultiPolyline() or []
                                    if not lines:
                                        single = track_feature.geometry().asPolyline()
                                        if single:
                                            lines = [single]
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
                                            lonl, latl = _to_wgs_xy_from_point(pt, ct_line)
                                            ET.SubElement(inner_lsg_extra, 'PNT', {'A': a_val, 'C': str(latl), 'D': str(lonl)})
                            else:
                                for track_feature in line_layer.getFeatures():
                                    match_ok = False
                                    for cand in ('ID', 'id', 'field_id'):
                                        if cand in line_names:
                                            try:
                                                match_ok = int(track_feature[cand]) == int(field_id)
                                            except Exception:
                                                match_ok = False
                                            break
                                    if not match_ok:
                                        continue
                                    track_name = track_feature['Name'] if 'Name' in line_names else ''
                                    ggp_element = ET.SubElement(pfd_element, 'GGP', {
                                        'A': next_ggp_id(),
                                        'B': track_name
                                    })


                                    lines = track_feature.geometry().asMultiPolyline() or []
                                    if not lines:
                                        single = track_feature.geometry().asPolyline()
                                        if single:
                                            lines = [single]
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
                                            lonl, latl = _to_wgs_xy_from_point(pt, ct_line)
                                            ET.SubElement(lsg_element_, 'PNT', {'A': a_val, 'C': str(latl), 'D': str(lonl)})
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
            self.iface.messageBar().pushMessage("Success", f"TASKDATA.XML geschrieben: {output_file_path}", level=Qgis.Success, duration=4)
        self.dlg.accept()
