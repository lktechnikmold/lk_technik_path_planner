# -*- coding: utf-8 -*-
"""
AgGPS-Import (Trimble / Case / New Holland u.a.).

Liest die Ordnerstruktur:
    AgGPS/Data/<Kunde>/<Betrieb>/<Feld>/
        boundary.shp      -> Feldgrenzen   (Spalten: id, Name, Area)
        swaths.shp        -> Fahrspuren    (Spalten: id, Name; id 0/1 = Gerade/Kurve)
        AreaFeature.shp   -> Flaechenhindernis
        PointFeature.shp  -> Punkthindernis

Jeder <Feld>-Ordner entspricht einem Feld. Die Feld-ID stammt aus boundary.shp
(Spalte id); fehlt sie, wird je Betrieb fortlaufend vergeben.
"""

import os

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsField, QgsFeature, QgsGeometry,
    QgsLayerTreeGroup, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsVectorFileWriter, QgsDistanceArea, QgsUnitTypes,
)
from qgis.PyQt.QtCore import QVariant


SHP_NAMES = ("boundary.shp", "swaths.shp", "AreaFeature.shp", "PointFeature.shp")


def _looks_like_aggps_data(d):
    """True, wenn d eine <Kunde>/<Betrieb>/<Feld>/*.shp-Struktur enthält."""
    try:
        for kunde in os.listdir(d):
            kp = os.path.join(d, kunde)
            if not os.path.isdir(kp):
                continue
            for betrieb in os.listdir(kp):
                bp = os.path.join(kp, betrieb)
                if not os.path.isdir(bp):
                    continue
                for feld in os.listdir(bp):
                    fp = os.path.join(bp, feld)
                    if not os.path.isdir(fp):
                        continue
                    if any(os.path.exists(os.path.join(fp, n)) for n in SHP_NAMES):
                        return True
    except Exception:
        pass
    return False


def detect_aggps_data_root(path):
    """Liefert das Data-Verzeichnis der AgGPS-Struktur oder None."""
    if not path or not os.path.isdir(path):
        return None
    cands = [
        os.path.join(path, "AgGPS", "Data"),
        os.path.join(path, "Data"),
        path,
    ]
    for c in cands:
        if os.path.isdir(c) and _looks_like_aggps_data(c):
            return c
    return None


def import_aggps(plugin, path, out_dir=None):
    """Importiert eine AgGPS-Ordnerstruktur. Gibt True bei Erfolg zurück."""
    project = QgsProject.instance()

    data_root = detect_aggps_data_root(path)
    if data_root is None:
        plugin.iface.messageBar().pushMessage(
            "Fehler",
            "Im gewählten Ordner wurde keine AgGPS-Struktur (Data/Kunde/Betrieb/Feld) gefunden.",
            level=Qgis.Warning, duration=6
        )
        return False

    # Ziel-KBS wie bei den anderen Importen
    src_default = QgsCoordinateReferenceSystem("EPSG:4326")
    use_project_crs = plugin.dlg.rb_import_project.isChecked()
    target_crs = project.crs() if use_project_crs else src_default
    crs_authid = target_crs.authid() if target_crs.isValid() else "EPSG:4326"

    # ---- Helfer ----
    def _norm_name(s):
        return " ".join((s or "").split())

    def _safe(name):
        return (name or "_untitled_").replace(os.sep, "_").replace("/", "_").strip()

    def _find_or_create_group(parent_group, name):
        wanted = _norm_name(name)
        for ch in parent_group.children():
            if isinstance(ch, QgsLayerTreeGroup) and _norm_name(ch.name()) == wanted:
                return ch
        return parent_group.addGroup(wanted)

    def _attr(feat, fmap, *names):
        for n in names:
            key = fmap.get(n.lower())
            if key is not None:
                try:
                    v = feat[key]
                except Exception:
                    v = None
                if v not in (None, ""):
                    return v
        return None

    def _open_shp(folder, fname):
        p = os.path.join(folder, fname)
        if not os.path.exists(p):
            return None
        lyr = QgsVectorLayer(p, fname, "ogr")
        return lyr if lyr.isValid() else None

    def _fmap(lyr):
        return {f.name().lower(): f.name() for f in lyr.fields()}

    def _transform_for(lyr):
        s = lyr.crs() if (lyr and lyr.crs().isValid()) else src_default
        if s != target_crs:
            return QgsCoordinateTransform(s, target_crs, project)
        return None

    def _geom_to_target(geom, ct, make_multi):
        if geom is None or geom.isEmpty():
            return None
        g = QgsGeometry(geom)
        if ct is not None:
            g.transform(ct)
        if make_multi and not g.isMultipart():
            g.convertToMultiType()
        return g

    def _area_calc(lyr):
        """QgsDistanceArea passend zur CRS des Shapefiles (ellipsoidisch, m²)."""
        s = lyr.crs() if (lyr and lyr.crs().isValid()) else src_default
        da = QgsDistanceArea()
        da.setSourceCrs(s, project.transformContext())
        ell = project.ellipsoid()
        da.setEllipsoid(ell if ell and ell != "NONE" else "WGS84")
        return da

    def _area_m2(da, geom):
        """Fläche immer aus der Geometrie berechnen (nie aus der Datei)."""
        if da is None or geom is None or geom.isEmpty():
            return 0.0
        try:
            raw = da.measureArea(geom)
            return float(da.convertAreaMeasurement(raw, QgsUnitTypes.AreaSquareMeters))
        except Exception:
            return 0.0

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
            QgsField("ID", QVariant.Int), QgsField("befahrbar", QVariant.Int),
        ]); al.updateFields()

        return {"Feldgrenzen": fl, "Fahrspuren": ll,
                "Punkthindernis": pl, "Flaechenhindernis": al}

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
                opts.attributesToExport = [
                    f.name() for f in mem_layer.fields() if f.name().lower() != "fid"
                ]
            except Exception:
                pass
            ret = QgsVectorFileWriter.writeAsVectorFormatV3(mem_layer, gpkg_path, tr_ctx, opts)
            res = ret[0] if isinstance(ret, (tuple, list)) else ret
            if res != QgsVectorFileWriter.NoError:
                new_layers[key] = mem_layer
                continue
            uri = f"{gpkg_path}|layername={key}"
            file_layer = QgsVectorLayer(uri, mem_layer.name(), "ogr")
            if file_layer.isValid():
                try:
                    parent = project.layerTreeRoot().findLayer(mem_layer.id()).parent()
                except Exception:
                    parent = None
                project.removeMapLayer(mem_layer.id())
                project.addMapLayer(file_layer, False)
                if parent and isinstance(parent, QgsLayerTreeGroup):
                    parent.addLayer(file_layer)
                else:
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

    # ---- Durchlauf Kunde / Betrieb / Feld ----
    any_field = False
    for kunde in sorted(os.listdir(data_root)):
        kp = os.path.join(data_root, kunde)
        if not os.path.isdir(kp):
            continue
        for betrieb in sorted(os.listdir(kp)):
            bp = os.path.join(kp, betrieb)
            if not os.path.isdir(bp):
                continue

            layers, key = _ensure_farm_layers(kunde, betrieb)
            used_ids = set(felder_by_key.get(key, {}).keys())
            next_id = (max(used_ids) + 1) if used_ids else 1

            for feld in sorted(os.listdir(bp)):
                fp = os.path.join(bp, feld)
                if not os.path.isdir(fp):
                    continue

                b_lyr = _open_shp(fp, "boundary.shp")
                s_lyr = _open_shp(fp, "swaths.shp")
                a_lyr = _open_shp(fp, "AreaFeature.shp")
                p_lyr = _open_shp(fp, "PointFeature.shp")
                if not any([b_lyr, s_lyr, a_lyr, p_lyr]):
                    continue

                # Feld-ID / Name bestimmen
                field_id = None
                field_name = _norm_name(feld)
                if b_lyr is not None:
                    bfmap = _fmap(b_lyr)
                    for bf in b_lyr.getFeatures():
                        rid = _attr(bf, bfmap, "id")
                        rnm = _attr(bf, bfmap, "Name")
                        if rid is not None:
                            try:
                                field_id = int(rid)
                            except Exception:
                                field_id = None
                        if rnm:
                            field_name = str(rnm).strip()
                        break

                if field_id is None or field_id in used_ids:
                    while next_id in used_ids:
                        next_id += 1
                    field_id = next_id
                used_ids.add(field_id)
                next_id = max(next_id, field_id + 1)

                felder_by_key.setdefault(key, {})[field_id] = field_name
                any_field = True

                # Feldgrenzen
                if b_lyr is not None:
                    ct = _transform_for(b_lyr)
                    da_b = _area_calc(b_lyr)
                    bfmap = _fmap(b_lyr)
                    feats = []
                    for bf in b_lyr.getFeatures():
                        raw_geom = bf.geometry()
                        g = _geom_to_target(raw_geom, ct, make_multi=True)
                        if g is None:
                            continue
                        # Fläche IMMER aus der Geometrie berechnen (nie aus der Datei)
                        flaeche = round(_area_m2(da_b, raw_geom), 2)
                        nm = _attr(bf, bfmap, "Name") or field_name
                        nf = QgsFeature(layers["Feldgrenzen"].fields())
                        nf.setGeometry(g)
                        nf.setAttribute("ID", int(field_id))
                        nf.setAttribute("Name", str(nm))
                        nf.setAttribute("Flaeche", flaeche)
                        feats.append(nf)
                    if feats:
                        layers["Feldgrenzen"].dataProvider().addFeatures(feats)

                # Fahrspuren
                if s_lyr is not None:
                    ct = _transform_for(s_lyr)
                    sfmap = _fmap(s_lyr)
                    feats = []
                    for sf in s_lyr.getFeatures():
                        g = _geom_to_target(sf.geometry(), ct, make_multi=True)
                        if g is None:
                            continue
                        nm = _attr(sf, sfmap, "Name") or "Fahrspur"
                        nf = QgsFeature(layers["Fahrspuren"].fields())
                        nf.setGeometry(g)
                        nf.setAttribute("ID", int(field_id))
                        nf.setAttribute("Name", str(nm))
                        nf.setAttribute("Segment", None)
                        feats.append(nf)
                    if feats:
                        layers["Fahrspuren"].dataProvider().addFeatures(feats)

                # Flaechenhindernis
                if a_lyr is not None:
                    ct = _transform_for(a_lyr)
                    feats = []
                    for af in a_lyr.getFeatures():
                        g = _geom_to_target(af.geometry(), ct, make_multi=True)
                        if g is None:
                            continue
                        nf = QgsFeature(layers["Flaechenhindernis"].fields())
                        nf.setGeometry(g)
                        nf.setAttribute("ID", int(field_id))
                        nf.setAttribute("befahrbar", 0)
                        feats.append(nf)
                    if feats:
                        layers["Flaechenhindernis"].dataProvider().addFeatures(feats)

                # Punkthindernis
                if p_lyr is not None:
                    ct = _transform_for(p_lyr)
                    pfmap = _fmap(p_lyr)
                    feats = []
                    for pf in p_lyr.getFeatures():
                        g = _geom_to_target(pf.geometry(), ct, make_multi=False)
                        if g is None:
                            continue
                        nm = _attr(pf, pfmap, "Name") or "Punkthindernis"
                        nf = QgsFeature(layers["Punkthindernis"].fields())
                        nf.setGeometry(g)
                        nf.setAttribute("ID", int(field_id))
                        nf.setAttribute("Name", str(nm))
                        nf.setAttribute("befahrbar", 0)
                        feats.append(nf)
                    if feats:
                        layers["Punkthindernis"].dataProvider().addFeatures(feats)

    if not any_field:
        plugin.iface.messageBar().pushMessage(
            "Hinweis", "Keine Felder in der AgGPS-Struktur gefunden.",
            level=Qgis.Info, duration=5
        )
        return False

    for layers in per_farm_layers.values():
        for lyr in layers.values():
            lyr.updateExtents()

    # ---- Felder-Katalog (Felder.csv) je Betrieb ----
    try:
        try:
            from .lk_technik_path_planner import (
                _felder_csv_path_in_dir, _read_felder_csv, _write_felder_csv,
                _load_felder_layer, FELDER_LAYER_NAME
            )
        except Exception:
            from lk_technik_path_planner import (
                _felder_csv_path_in_dir, _read_felder_csv, _write_felder_csv,
                _load_felder_layer, FELDER_LAYER_NAME
            )

        for key, rows in felder_by_key.items():
            frm_group = per_farm_groups.get(key)
            if frm_group is None:
                continue
            ctr_name, frm_name = key
            if out_dir:
                base = os.path.join(out_dir, _safe(ctr_name), _safe(frm_name))
                csv_path = _felder_csv_path_in_dir(base)
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
                for fid in sorted(rows.keys()):
                    f = QgsFeature(mem_felder.fields())
                    f.setAttribute("id", int(fid))
                    f.setAttribute("Name", rows.get(fid, ""))
                    feats.append(f)
                if feats:
                    dpf.addFeatures(feats)
                mem_felder.updateExtents()
                project.addMapLayer(mem_felder, False)
                frm_group.insertLayer(0, mem_felder)

        for frm_group in per_farm_groups.values():
            try:
                plugin._reorder_frm_group_layers(frm_group)
            except Exception:
                pass
        try:
            plugin._apply_field_dropdowns()
        except Exception:
            pass
    except Exception:
        pass

    plugin.iface.messageBar().pushMessage(
        "Erfolgreich", "AgGPS-Daten wurden importiert.",
        level=Qgis.Success, duration=5
    )
    return True
