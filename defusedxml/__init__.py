# defusedxml (vendored subset)
#
# Copyright (c) 2013-2020 by Christian Heimes <christian@python.org>
# Licensed to PSF under a Contributor Agreement.
# See https://www.python.org/psf/license for licensing details. Full text
# in LICENSE next to this file.
#
# This is a trimmed-down vendored copy of defusedxml
# (https://github.com/tiran/defusedxml, unmodified ElementTree.py and
# common.py), bundled with this plugin so that reading externally supplied
# TASKDATA.XML / MasterData.xml files does not depend on defusedxml being
# separately installed in the QGIS Python environment. Only the
# ElementTree-based safe parsing facade is vendored (this plugin does not
# use defusedxml's minidom/sax/pulldom/expatbuilder wrappers), so
# defuse_stdlib() from upstream __init__.py was intentionally left out.
"""Defuse XML bomb denial of service vulnerabilities (vendored subset)."""
from .common import (
    DefusedXmlException,
    DTDForbidden,
    EntitiesForbidden,
    ExternalReferenceForbidden,
    NotSupportedError,
)

__version__ = "0.8.0rc2"

__all__ = [
    "DefusedXmlException",
    "DTDForbidden",
    "EntitiesForbidden",
    "ExternalReferenceForbidden",
    "NotSupportedError",
]
