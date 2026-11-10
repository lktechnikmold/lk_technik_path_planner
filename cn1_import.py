# -*- coding: utf-8 -*-
"""
CN1-Import (CNH: Case IH / Steyr, New Holland).

Liest einen <ID>.cn1-Ordner (siehe cn1_format.py) und legt je Kunde/Betrieb
die üblichen Layer an:
    <Feld>.bnd  -> Feldgrenzen
    <Feld>.wob  -> Punkthindernis (Punkte) / Flaechenhindernis (Flächen)
    <Spur>.ags  -> Fahrspuren
CN1 kennt kein "befahrbar"-Kennzeichen; Hindernisse werden als nicht
befahrbar importiert. Feld-IDs werden je Betrieb fortlaufend vergeben.

Hinweis zu nosec-Markierungen B110: breite except Exception: pass-Bloecke werden hier
bewusst verwendet, um robust gegenueber Unterschieden zwischen QGIS-/PyQt-
Versionen zu bleiben. Es werden dabei keine sicherheitsrelevanten Pruefungen
uebersprungen.
"""

import glob
import os

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, QgsPointXY,
    QgsLayerTreeGroup, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsVectorFileWriter, QgsDistanceArea, QgsUnitTypes,
)
from qgis.PyQt.QtCore import QVariant

try:
    from . import cn1_format as cn1
except Exception:
    import cn1_format as cn1


def _read_cn1(root):
    """
    -> Liste von Feldern:
       {"grower", "farm", "name", "rings": [(kind, [(lat, lon)])],
        "objects": [(name, kind, pts)], "lines": [(name, kind, pts)]}
    """
    shared = os.path.join(root, "shared")
    growers = {}
    for rec in cn1.read_records(os.path.join(shared, "growers.gws")):
        if rec["type"] == cn1.T_GROWER:
            growers[cn1.id_text(rec["id"])] = cn1.name_text(rec["payload"][:cn1.NAME_LEN])

    fields = []
    for fms in sorted(glob.glob(os.path.join(shared, "*.FMS"))):
        recs = [r for r in cn1.read_records(fms) if r["type"] == cn1.T_FARM]
        if not recs:
            continue
        rec = recs[0]
        farm_id = cn1.id_text(rec["id"]) or os.path.splitext(os.path.basename(fms))[0]
        pl = rec["payload"]
        farm_name = cn1.name_text(pl[:cn1.NAME_LEN]) or farm_id
        grower_id = cn1.id_text(rec["parent"]) or cn1.id_text(pl[cn1.NAME_LEN:cn1.NAME_LEN + 8])
        grower_name = (growers.get(grower_id)
                       or cn1.name_text(pl[cn1.NAME_LEN + 8:2 * cn1.NAME_LEN + 8])
                       or grower_id or "Kunde")

        fmd = os.path.join(shared, f"{farm_id}.FMD")
        for fls in sorted(glob.glob(os.path.join(fmd, "*.FLS"))):
            frecs = [r for r in cn1.read_records(fls) if r["type"] == cn1.T_FIELD]
            if not frecs:
                continue
            field_id = cn1.id_text(frecs[0]["id"]) or os.path.splitext(os.path.basename(fls))[0]
            field = {
                "grower": grower_name,
                "farm": farm_name,
                "name": cn1.name_text(frecs[0]["payload"][:cn1.NAME_LEN]) or field_id,
                "rings": [], "objects": [], "lines": [],
            }
            fld = os.path.join(fmd, f"{field_id}.FLD")

            for rec in cn1.read_records(os.path.join(fld, f"{field_id}.bnd")):
                if rec["type"] != cn1.T_BOUNDARY:
                    continue
                try:
                    _, kind, pts = cn1.parse_geometry_payload(rec["payload"])
                except Exception:  # nosec B112
                    continue
                if len(pts) >= 3:
                    field["rings"].append((kind, pts))

            for rec in cn1.read_records(os.path.join(fld, f"{field_id}.wob")):
                if rec["type"] != cn1.T_OBJECT:
                    continue
                try:
                    field["objects"].append(cn1.parse_geometry_payload(rec["payload"]))
                except Exception:  # nosec B112
                    continue

            for ags in sorted(glob.glob(os.path.join(fld, "*.ags"))):
                for rec in cn1.read_records(ags):
                    if rec["type"] != cn1.T_GUIDANCE:
                        continue
                    try:
                        name, kind, _heading, pts = cn1.parse_guidance_payload(rec["payload"])
                    except Exception:  # nosec B112
                        continue
                    if len(pts) >= 2:
                        field["lines"].append((name, kind, pts))

            fields.append(field)
    return fields


def import_cn1(plugin, path, out_dir=None):
    """Importiert einen CN1-Ordner. Gibt True bei Erfolg zurück."""
    try:
        from .lk_technik_path_planner import (
            _group_polygon_rings, _felder_csv_path_in_dir, _read_felder_csv, _write_felder_csv,
            _load_felder_layer, FELDER_LAYER_NAME, _safe, _norm_name,
        )
    except Exception:
        from lk_technik_path_planner import (
            _group_polygon_rings, _felder_csv_path_in_dir, _read_felder_csv, _write_felder_csv,
            _load_felder_layer, FELDER_LAYER_NAME, _safe, _norm_name,
        )

    project = QgsProject.instance()
    root = cn1.find_cn1_root(path)
    if root is None:
        return False
    fields = _read_cn1(root)
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
        """[(lat, lon)] -> [QgsPointXY] im Ziel-KBS."""
        out = []
        for lat, lon in pts:
            p = QgsPointXY(lon, lat)
            if ct is not None:
                p = ct.transform(p)
            out.append(p)
        return out

    def _wgs_xy(pts):
        return [QgsPointXY(lon, lat) for lat, lon in pts]

    def _area_m2(rings_wgs):
        try:
            g = QgsGeometry.fromPolygonXY(rings_wgs)
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
        new_layers = {}
        tr_ctx = project.transformContext()
        for key, mem_layer in layers_dict.items():
            gpkg_path = os.path.join(base, f"{_safe(key)}.gpkg")
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = key
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

    for field in fields:
        layers, key = _ensure_farm_layers(field["grower"], field["farm"])
        used = felder_by_key[key]
        field_id = (max(used) + 1) if used else 1
        used[field_id] = field["name"]

        # Feldgrenzen (Art 1 wird als Loch gewertet - bisher nur Außenringe beobachtet)
        items = [("2" if kind == 1 else "1", _wgs_xy(pts)) for kind, pts in field["rings"]]
        feats = []
        for grp in _group_polygon_rings(items):
            rings_wgs = [items[i][1] for i in grp]
            g = QgsGeometry.fromPolygonXY([_xy([(p.y(), p.x()) for p in r]) for r in rings_wgs])
            g.convertToMultiType()
            nf = QgsFeature(layers["Feldgrenzen"].fields())
            nf.setGeometry(g)
            nf.setAttribute("ID", int(field_id))
            nf.setAttribute("Name", field["name"])
            nf.setAttribute("Flaeche", _area_m2(rings_wgs))
            feats.append(nf)
        if feats:
            layers["Feldgrenzen"].dataProvider().addFeatures(feats)

        # Hindernisse
        p_feats, a_feats = [], []
        for name, kind, pts in field["objects"]:
            if kind == cn1.OBJ_AREA or len(pts) >= 3:
                if len(pts) < 3:
                    continue
                g = QgsGeometry.fromPolygonXY([_xy(pts)])
                g.convertToMultiType()
                nf = QgsFeature(layers["Flaechenhindernis"].fields())
                nf.setGeometry(g)
                nf.setAttribute("ID", int(field_id))
                nf.setAttribute("befahrbar", 0)
                a_feats.append(nf)
            elif pts:
                nf = QgsFeature(layers["Punkthindernis"].fields())
                nf.setGeometry(QgsGeometry.fromPointXY(_xy(pts[:1])[0]))
                nf.setAttribute("ID", int(field_id))
                nf.setAttribute("Name", name or "Punkthindernis")
                nf.setAttribute("befahrbar", 0)
                p_feats.append(nf)
        if p_feats:
            layers["Punkthindernis"].dataProvider().addFeatures(p_feats)
        if a_feats:
            layers["Flaechenhindernis"].dataProvider().addFeatures(a_feats)

        # Fahrspuren
        l_feats = []
        for name, _kind, pts in field["lines"]:
            g = QgsGeometry.fromMultiPolylineXY([_xy(pts)])
            nf = QgsFeature(layers["Fahrspuren"].fields())
            nf.setGeometry(g)
            nf.setAttribute("ID", int(field_id))
            nf.setAttribute("Name", name or "Fahrspur")
            nf.setAttribute("Segment", None)
            l_feats.append(nf)
        if l_feats:
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
