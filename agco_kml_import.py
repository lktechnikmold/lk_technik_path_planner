# -*- coding: utf-8 -*-
"""
AGCO-KML-Import (Fendt / Valtra / Massey Ferguson).

Liest eine Field_<Name>.kml, eine Fendt-ZIP oder einen Ordner mit mehreren
davon (Format siehe agco_kml_format.py) und legt je Kunde/Betrieb die üblichen
Layer an. Kunde und Betrieb stammen aus der TASKDATA in der ZIP (bzw. neben der
.kml); fehlt sie, landen die Felder unter "Unbenannter Kunde" /
"Unbenannter Betrieb". Feld-IDs
werden je Betrieb fortlaufend vergeben.

Hinweis zu nosec-Markierungen B110: breite except Exception: pass-Bloecke werden hier
bewusst verwendet, um robust gegenueber Unterschieden zwischen QGIS-/PyQt-
Versionen zu bleiben. Es werden dabei keine sicherheitsrelevanten Pruefungen
uebersprungen.
"""

import os

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, QgsPointXY,
    QgsLayerTreeGroup, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsVectorFileWriter, QgsDistanceArea, QgsUnitTypes,
)
from qgis.PyQt.QtCore import QVariant

try:
    from . import agco_kml_format as kml
except Exception:
    import agco_kml_format as kml


def _read_fields(paths):
    """-> Liste von Feldern {"grower", "farm", "name", "boundaries", "areas", "points", "lines"}."""
    fields, seen = [], set()
    for path in ([paths] if isinstance(paths, str) else paths):
        for item in kml.read_sources(path):
            if item[0] not in seen:          # dieselbe KML in mehreren Quellen nur einmal
                seen.add(item[0])
                field = _field(*item)
                if field is not None:
                    fields.append(field)
    return fields


def _field(kml_data, ini_data, task_data, stem):
    """Ein Feld aus KML (+ .ini / TASKDATA) oder None, wenn das KML unlesbar ist."""
    try:
        content = kml.parse_kml(kml_data)
    except Exception:
        return None
    name = (content["name"] or (kml.ini_name(ini_data) if ini_data else "")
            or kml.name_from_stem(stem) or "Feld")
    grower, farm = kml.taskdata_farm(task_data, name) if task_data else (None, None)
    content.update(grower=grower or kml.DEFAULT_CUSTOMER, farm=farm or kml.DEFAULT_FARM, name=name)
    return content


def import_agco_kml(plugin, path, out_dir=None):
    """Importiert AGCO-KML-Felder (path: Datei, ZIP, Ordner oder Liste davon). True bei Erfolg."""
    try:
        from .lk_technik_path_planner import (
            _felder_csv_path_in_dir, _read_felder_csv, _write_felder_csv,
            _load_felder_layer, FELDER_LAYER_NAME, _safe, _norm_name,
        )
    except Exception:
        from lk_technik_path_planner import (
            _felder_csv_path_in_dir, _read_felder_csv, _write_felder_csv,
            _load_felder_layer, FELDER_LAYER_NAME, _safe, _norm_name,
        )

    project = QgsProject.instance()
    fields = _read_fields(path)
    if not fields:
        return False

    src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    use_project_crs = plugin.dlg.rb_import_project.isChecked()
    target_crs = project.crs() if use_project_crs else src_crs
    crs_authid = target_crs.authid() if target_crs.isValid() else "EPSG:4326"
    ct = QgsCoordinateTransform(src_crs, target_crs, project) if target_crs != src_crs else None

    da = QgsDistanceArea()
    da.setSourceCrs(src_crs, project.transformContext())
    da.setEllipsoid("WGS84")

    def _xy(pts):
        """[(lon, lat)] -> [QgsPointXY] im Ziel-KBS."""
        out = []
        for lon, lat in pts:
            p = QgsPointXY(lon, lat)
            if ct is not None:
                p = ct.transform(p)
            out.append(p)
        return out

    def _area_m2(rings):
        try:
            g = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in r] for r in rings])
            return round(float(da.convertAreaMeasurement(da.measureArea(g), QgsUnitTypes.AreaSquareMeters)), 2)
        except Exception:
            return 0.0

    def _find_or_create_group(parent_group, name):
        wanted = _norm_name(name)
        for ch in parent_group.children():
            if isinstance(ch, QgsLayerTreeGroup) and _norm_name(ch.name()) == wanted:
                return ch
        return parent_group.addGroup(wanted)

    def _create_memory_layers():
        fl = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "Feldgrenzen", "memory")
        fl.dataProvider().addAttributes([
            QgsField("ID", QVariant.Int), QgsField("Name", QVariant.String),
            QgsField("Flaeche", QVariant.Double),
        ]); fl.updateFields()
        ll = QgsVectorLayer(f"MultiLineString?crs={crs_authid}", "Fahrspuren", "memory")
        ll.dataProvider().addAttributes([
            QgsField("ID", QVariant.Int), QgsField("Name", QVariant.String),
            QgsField("Segment", QVariant.String),
        ]); ll.updateFields()
        pl = QgsVectorLayer(f"Point?crs={crs_authid}", "Punkthindernis", "memory")
        pl.dataProvider().addAttributes([
            QgsField("ID", QVariant.Int), QgsField("Name", QVariant.String),
            QgsField("befahrbar", QVariant.Int),
        ]); pl.updateFields()
        al = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "Flaechenhindernis", "memory")
        al.dataProvider().addAttributes([
            QgsField("ID", QVariant.Int), QgsField("Name", QVariant.String),
            QgsField("befahrbar", QVariant.Int),
        ]); al.updateFields()
        return {"Feldgrenzen": fl, "Fahrspuren": ll, "Punkthindernis": pl, "Flaechenhindernis": al}

    def _persist_farm_layers(layers_dict, ctr_name, frm_name, frm_group):
        if not out_dir:
            return layers_dict
        base = os.path.join(out_dir, _safe(ctr_name), _safe(frm_name))
        os.makedirs(base, exist_ok=True)
        plugin._prepare_import_dir(base, layers_dict.values())
        new_layers = {}
        tr_ctx = project.transformContext()
        for key, mem_layer in layers_dict.items():
            gpkg_path = os.path.join(base, f"{_safe(key)}.gpkg")
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = key
            plugin._gpkg_write_options(opts, gpkg_path)
            try:
                opts.attributesToExport = [f.name() for f in mem_layer.fields() if f.name().lower() != "fid"]
            except Exception:  # nosec B110
                pass
            ret = QgsVectorFileWriter.writeAsVectorFormatV3(mem_layer, gpkg_path, tr_ctx, opts)
            res = ret[0] if isinstance(ret, (tuple, list)) else ret
            if res != QgsVectorFileWriter.NoError:
                new_layers[key] = mem_layer
                continue
            file_layer = QgsVectorLayer(f"{gpkg_path}|layername={key}", mem_layer.name(), "ogr")
            if file_layer.isValid():
                project.removeMapLayer(mem_layer.id())
                project.addMapLayer(file_layer, False)
                frm_group.addLayer(file_layer)
                plugin._apply_predefined_style(file_layer)
                if key == "Feldgrenzen":
                    plugin._apply_feldgrenzen_color(file_layer, frm_group)
                new_layers[key] = file_layer
            else:
                new_layers[key] = mem_layer
        plugin._reorder_frm_group_layers(frm_group)
        return new_layers

    root_group = project.layerTreeRoot()
    per_farm_layers = {}
    per_farm_groups = {}
    felder_by_key = {}

    def _ensure_farm_layers(ctr_name, frm_name):
        key = (ctr_name, frm_name)
        if key in per_farm_layers:
            return per_farm_layers[key], key
        ctr_group = _find_or_create_group(root_group, ctr_name)
        frm_group = _find_or_create_group(ctr_group, frm_name)
        layers = _create_memory_layers()
        for lyr in layers.values():
            project.addMapLayer(lyr, False)
            frm_group.addLayer(lyr)
        layers = _persist_farm_layers(layers, ctr_name, frm_name, frm_group)
        per_farm_layers[key] = layers
        per_farm_groups[key] = frm_group
        felder_by_key.setdefault(key, {})
        return layers, key

    def _polygon_feature(lyr, rings, field_id, name=None, extra=None):
        g = QgsGeometry.fromPolygonXY([_xy(r) for r in rings])
        g.convertToMultiType()
        nf = QgsFeature(lyr.fields())
        nf.setGeometry(g)
        nf.setAttribute("ID", int(field_id))
        if name is not None:
            nf.setAttribute("Name", name)
        for k, v in (extra or {}).items():
            nf.setAttribute(k, v)
        return nf

    for field in fields:
        layers, key = _ensure_farm_layers(field["grower"], field["farm"])
        used = felder_by_key[key]
        field_id = (max(used) + 1) if used else 1
        used[field_id] = field["name"]

        layers["Feldgrenzen"].dataProvider().addFeatures([
            _polygon_feature(layers["Feldgrenzen"], rings, field_id, field["name"], {"Flaeche": _area_m2(rings)})
            for rings in field["boundaries"]])

        # Das KML kennt kein "befahrbar" für Flächen: der Fendt schreibt nur
        # unbefahrbare Hindernisse (und Löcher der Feldgrenze) als Fläche.
        layers["Flaechenhindernis"].dataProvider().addFeatures([
            _polygon_feature(layers["Flaechenhindernis"], rings, field_id, name or None, {"befahrbar": 0})
            for name, rings in field["areas"]])

        p_feats = []
        for name, passable, pt in field["points"]:
            nf = QgsFeature(layers["Punkthindernis"].fields())
            nf.setGeometry(QgsGeometry.fromPointXY(_xy([pt])[0]))
            nf.setAttribute("ID", int(field_id))
            nf.setAttribute("Name", name or "Punkthindernis")
            nf.setAttribute("befahrbar", int(passable))
            p_feats.append(nf)
        layers["Punkthindernis"].dataProvider().addFeatures(p_feats)

        l_feats = []
        for name, _kind, pts in field["lines"]:
            nf = QgsFeature(layers["Fahrspuren"].fields())
            nf.setGeometry(QgsGeometry.fromMultiPolylineXY([_xy(pts)]))
            nf.setAttribute("ID", int(field_id))
            nf.setAttribute("Name", name or "Fahrspur")
            nf.setAttribute("Segment", None)
            l_feats.append(nf)
        layers["Fahrspuren"].dataProvider().addFeatures(l_feats)

    for layers in per_farm_layers.values():
        for lyr in layers.values():
            lyr.updateExtents()

    # ---- Felder-Katalog (Felder.csv) je Betrieb ----
    try:
        for key, rows in felder_by_key.items():
            frm_group = per_farm_groups.get(key)
            if frm_group is None:
                continue
            ctr_name, frm_name = key
            if out_dir:
                csv_path = _felder_csv_path_in_dir(os.path.join(out_dir, _safe(ctr_name), _safe(frm_name)))
                merged = _read_felder_csv(csv_path)
                for fid, nm in rows.items():
                    if fid not in merged or (not merged.get(fid) and nm):
                        merged[fid] = nm
                _write_felder_csv(csv_path, merged)
                try:
                    plugin._recreate_felder_layer(frm_group, csv_path)
                except Exception:
                    felder_layer = _load_felder_layer(csv_path)
                    if felder_layer is not None:
                        project.addMapLayer(felder_layer, False)
                        frm_group.insertLayer(0, felder_layer)
            else:
                mem_felder = QgsVectorLayer("None", FELDER_LAYER_NAME, "memory")
                dpf = mem_felder.dataProvider()
                dpf.addAttributes([QgsField("id", QVariant.Int), QgsField("Name", QVariant.String)])
                mem_felder.updateFields()
                feats = []
                for fid in sorted(rows):
                    f = QgsFeature(mem_felder.fields())
                    f.setAttribute("id", int(fid))
                    f.setAttribute("Name", rows.get(fid, ""))
                    feats.append(f)
                if feats:
                    dpf.addFeatures(feats)
                project.addMapLayer(mem_felder, False)
                frm_group.insertLayer(0, mem_felder)

        for frm_group in per_farm_groups.values():
            try:
                plugin._reorder_frm_group_layers(frm_group)
            except Exception:  # nosec B110
                pass
        try:
            plugin._apply_field_dropdowns()
        except Exception:  # nosec B110
            pass
    except Exception:  # nosec B110
        pass

    return True
