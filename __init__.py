
# -*- coding: utf-8 -*-
def classFactory(iface):
    from .lk_technik_path_planner import LkTechnikPathPlanner
    return LkTechnikPathPlanner(iface)
