# -*- coding: utf-8 -*-
"""
Import mehrerer Lenksystemdateien in einem Durchgang.

Jede Datei wird zuerst mit ihrem eigenen Importer in temporäre Layer gelesen.
Danach werden alle Betriebe mit demselben Kunden- und Betriebsnamen
zusammengeführt: ein Layersatz je Betrieb, Feld-IDs fortlaufend neu
vergeben (die IDs der einzelnen Dateien überschneiden sich sonst), und erst
dann als GPKG + Felder.csv in den Zielordner geschrieben.

Hinweis zu nosec-Markierungen B110: breite except Exception: pass-Bloecke werden hier
bewusst verwendet, um robust gegenueber Unterschieden zwischen QGIS-/PyQt-
Versionen zu bleiben. Es werden dabei keine sicherheitsrelevanten Pruefungen
uebersprungen.
"""

import os

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField, QgsFields, QgsFeature, QgsGeometry, QgsLayerTreeGroup,
    QgsVectorFileWriter, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

try:
    from . import translations
except Exception:
    import translations

DATA_LAYERS = ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis")


def _helpers():
    try:
        from . import lk_technik_path_planner as m
    except Exception:
        import lk_technik_path_planner as m
    return m


def collect_new_layers(before_ids):
    """
    Layer, die seit before_ids ins Projekt gekommen sind, je Betrieb:
    {(Kunde, Betrieb): {"Feldgrenzen": layer, ..., "Felder": layer}}
    """
    m = _helpers()
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    out = {}
    for lid, lyr in project.mapLayers().items():
        if lid in before_ids or not isinstance(lyr, QgsVectorLayer):
            continue
        node = root.findLayer(lid)
        frm = node.parent() if node is not None else None
        ctr = frm.parent() if isinstance(frm, QgsLayerTreeGroup) else None
        if not isinstance(ctr, QgsLayerTreeGroup) or ctr is root or ctr.parent() is None:
            continue
        name = translations.canonical_layer_name(lyr.name())
        if name not in DATA_LAYERS and name != m.FELDER_LAYER_NAME:
            continue
        key = (m._norm_name(ctr.name()), m._norm_name(frm.name()))
        out.setdefault(key, {})[name] = lyr
    return out


def _remove_layers(layer_ids):
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    groups = []
    for lid in layer_ids:
        node = root.findLayer(lid)
        if node is not None and isinstance(node.parent(), QgsLayerTreeGroup):
            groups.append(node.parent())
    project.removeMapLayers(list(layer_ids))
    # leer gewordene Betriebs- und Kundengruppen entfernen
    for frm in groups:
        try:
            ctr = frm.parent()
            if not frm.children() and isinstance(ctr, QgsLayerTreeGroup):
                ctr.removeChildNode(frm)
                if not ctr.children() and ctr is not root and isinstance(ctr.parent(), QgsLayerTreeGroup):
                    ctr.parent().removeChildNode(ctr)
        except Exception:  # nosec B110 - Gruppe bereits entfernt
            pass


def merge_batches(batches):
    """
    batches: Liste von collect_new_layers()-Ergebnissen (je importierter Datei).
    -> {(Kunde, Betrieb): {"rows": {id: Name}, "feats": {layer: [QgsFeature]}, "template": {layer: layer}}}
    Die Layer der Durchgänge werden danach aus dem Projekt entfernt.
    """
    m = _helpers()
    farms = {}
    used_ids = []
    for batch in batches:
        for key, layers in batch.items():
            farm = farms.setdefault(key, {"rows": {}, "feats": {n: [] for n in DATA_LAYERS}, "template": {}})
            rows = {}
            felder = layers.get(m.FELDER_LAYER_NAME)
            if felder is not None:
                for f in felder.getFeatures():
                    try:
                        rows[int(f["id"])] = "" if m._is_nullish(f["Name"]) else str(f["Name"])
                    except Exception:  # nosec B112
                        continue
            old_ids = set(rows)
            for name in DATA_LAYERS:
                lyr = layers.get(name)
                if lyr is None:
                    continue
                for f in lyr.getFeatures():
                    try:
                        old_ids.add(int(f["ID"]))
                    except Exception:  # nosec B112
                        continue
            next_id = max(farm["rows"], default=0) + 1
            id_map = {}
            for old in sorted(old_ids):
                id_map[old] = next_id
                farm["rows"][next_id] = rows.get(old, "")
                next_id += 1
            for name in DATA_LAYERS:
                lyr = layers.get(name)
                if lyr is None:
                    continue
                if name not in farm["template"]:
                    # nur Kenndaten merken - der Layer wird gleich aus dem Projekt entfernt
                    farm["template"][name] = (QgsWkbTypes.displayString(lyr.wkbType()),
                                              lyr.crs().authid() or "EPSG:4326", QgsFields(lyr.fields()))
                for f in lyr.getFeatures():
                    nf = QgsFeature(f)
                    try:
                        nf.setAttribute("ID", id_map.get(int(f["ID"])))
                    except Exception:  # nosec B112
                        continue
                    farm["feats"][name].append((nf, QgsFields(lyr.fields())))
            used_ids.extend(lyr.id() for lyr in layers.values())
    _remove_layers(used_ids)
    return farms


def _memory_layer(name, template):
    wkb, authid, fields = template
    lyr = QgsVectorLayer(f"{wkb}?crs={authid}", name, "memory")
    lyr.dataProvider().addAttributes([QgsField(f) for f in fields if f.name().lower() != "fid"])
    lyr.updateFields()
    return lyr


def write_farms(plugin, farms, out_dir=None):
    """Legt je Betrieb einen Layersatz an (GPKG bzw. temporär) und den Felder-Katalog."""
    m = _helpers()
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    def _group(parent, name):
        wanted = m._norm_name(name)
        for ch in parent.children():
            if isinstance(ch, QgsLayerTreeGroup) and m._norm_name(ch.name()) == wanted:
                return ch
        return parent.addGroup(wanted)

    for (ctr_name, frm_name), farm in farms.items():
        frm_group = _group(_group(root, ctr_name), frm_name)
        layers = {}
        for name in DATA_LAYERS:
            template = farm["template"].get(name)
            if template is None:
                continue
            lyr = _memory_layer(name, template)
            target = lyr.fields()
            feats = []
            multi = QgsWkbTypes.isMultiType(lyr.wkbType())
            for src, src_fields in farm["feats"][name]:
                nf = QgsFeature(target)
                g = QgsGeometry(src.geometry())
                if multi and not g.isEmpty() and not g.isMultipart():
                    g.convertToMultiType()
                nf.setGeometry(g)
                for fld in target:
                    i = src_fields.indexOf(fld.name())
                    if i >= 0:
                        nf.setAttribute(fld.name(), src.attribute(i))
                feats.append(nf)
            lyr.dataProvider().addFeatures(feats)
            lyr.updateExtents()
            project.addMapLayer(lyr, False)
            frm_group.addLayer(lyr)
            layers[name] = lyr

        if out_dir:
            base = os.path.join(out_dir, m._safe(ctr_name), m._safe(frm_name))
            os.makedirs(base, exist_ok=True)
            plugin._prepare_import_dir(base, layers.values())
            for key, mem in list(layers.items()):
                gpkg = os.path.join(base, f"{m._safe(key)}.gpkg")
                opts = QgsVectorFileWriter.SaveVectorOptions()
                opts.driverName = "GPKG"
                opts.layerName = key
                plugin._gpkg_write_options(opts, gpkg)
                opts.attributesToExport = [f.name() for f in mem.fields() if f.name().lower() != "fid"]
                ret = QgsVectorFileWriter.writeAsVectorFormatV3(mem, gpkg, project.transformContext(), opts)
                if (ret[0] if isinstance(ret, (tuple, list)) else ret) != QgsVectorFileWriter.NoError:
                    continue
                file_layer = QgsVectorLayer(f"{gpkg}|layername={key}", mem.name(), "ogr")
                if not file_layer.isValid():
                    continue
                project.removeMapLayer(mem.id())
                project.addMapLayer(file_layer, False)
                frm_group.addLayer(file_layer)
                plugin._apply_predefined_style(file_layer)
                if key == "Feldgrenzen":
                    plugin._apply_feldgrenzen_color(file_layer, frm_group)
            csv_path = m._felder_csv_path_in_dir(base)
            merged = m._read_felder_csv(csv_path)
            for fid, nm in farm["rows"].items():
                if fid not in merged or (not merged.get(fid) and nm):
                    merged[fid] = nm
            m._write_felder_csv(csv_path, merged)
            plugin._recreate_felder_layer(frm_group, csv_path)
        else:
            felder = QgsVectorLayer("None", m.FELDER_LAYER_NAME, "memory")
            felder.dataProvider().addAttributes([QgsField("id", QVariant.Int), QgsField("Name", QVariant.String)])
            felder.updateFields()
            feats = []
            for fid in sorted(farm["rows"]):
                f = QgsFeature(felder.fields())
                f.setAttribute("id", int(fid))
                f.setAttribute("Name", farm["rows"][fid])
                feats.append(f)
            felder.dataProvider().addFeatures(feats)
            project.addMapLayer(felder, False)
            frm_group.insertLayer(0, felder)
        try:
            plugin._reorder_frm_group_layers(frm_group)
        except Exception:  # nosec B110
            pass
    try:
        plugin._apply_field_dropdowns()
    except Exception:  # nosec B110
        pass
