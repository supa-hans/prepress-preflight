"""Rearrange a page in memory so part of it can be rendered on its own.

Everything here works on a second, in-memory handle of the file
(Document.scratch). It is never saved and the file on disk is never written.
"""
from __future__ import annotations

import numpy as np
import pymupdf
from .document import device_matrix

NORMAL_GS = "PreflightNormal"
FORM_NAME = "PreflightForm"


class PageRebuild:
    """Rearranges one page of the in-memory scratch copy. Never saved."""

    def __init__(self, doc, pno):
        self.doc, self.pno = doc, pno
        self.page_xref = doc.page_xref(pno)
        self.pool: list[int] = []  # spare objects: [0] page content, [k] stand-in for form k
        self.forms: list[tuple[int, int]] = []
        self._with_normal: set = set()
        self._contents = None
        xref = self.page_xref
        for _ in range(50):  # resources may be inherited from the page tree
            if doc.xref_get_key(xref, "Resources")[0] in ("xref", "dict"):
                break
            kind, value = doc.xref_get_key(xref, "Parent")
            if kind != "xref":
                raise ValueError("page resources not found")
            xref = int(value.split()[0])
        self.page_resources = self._locate(xref, "", "Resources")

    def _locate(self, xref, path, key):
        """Where the dictionary under `key` lives: (object number, key path prefix)."""
        kind, value = self.doc.xref_get_key(xref, path + key)
        if kind == "xref":
            return int(value.split()[0]), ""
        if kind == "dict":
            return xref, path + key + "/"
        raise ValueError(f"{key} not found")

    def _holder(self, k):
        while len(self.pool) <= k:
            xref = self.doc.get_new_xref()
            self.doc.update_object(xref, "<<>>")
            self.pool.append(xref)
        return self.pool[k]

    def _ensure_normal(self, where):
        """Give a resource dictionary a Normal graphics state to paint the object with."""
        if where in self._with_normal:
            return
        xref, path = where
        if self.doc.xref_get_key(xref, path + "ExtGState")[0] in ("xref", "dict"):
            gx, gp = self._locate(xref, path, "ExtGState")
            self.doc.xref_set_key(gx, gp + NORMAL_GS, "<</BM/Normal>>")
        else:
            self.doc.xref_set_key(xref, path + "ExtGState", f"<</{NORMAL_GS}<</BM/Normal>>>>")
        self._with_normal.add(where)

    def prepare(self, chain):
        """Find each form on the way to the object and name a stand-in beside it."""
        resources = [self.page_resources]
        self.forms = []
        for k, frame in enumerate(chain):
            xx, xp = self._locate(*resources[k], "XObject")
            kind, value = self.doc.xref_get_key(xx, xp + frame.name)
            if kind != "xref":
                raise ValueError("form not found")
            form = int(value.split()[0])
            own = self.doc.xref_get_key(form, "Resources")[0] in ("xref", "dict")
            resources.append(self._locate(form, "", "Resources") if own else resources[k])
            holder = self._holder(k + 1)
            self.doc.xref_set_key(xx, xp + f"{FORM_NAME}{k + 1}", f"{holder} 0 R")
            self.forms.append((holder, form))
        for where in resources:
            self._ensure_normal(where)

    def set_page_content(self, data):
        self.doc.update_stream(self._holder(0), data)
        self.doc.xref_set_key(self.page_xref, "Contents", f"{self._holder(0)} 0 R")

    def form_xref(self, names):
        """Object number of the form reached from the page through these XObject names."""
        where, form = self.page_resources, None
        for name in names:
            xx, xp = self._locate(*where, "XObject")
            kind, value = self.doc.xref_get_key(xx, xp + name)
            if kind != "xref":
                raise ValueError("form not found")
            form = int(value.split()[0])
            if self.doc.xref_get_key(form, "Resources")[0] in ("xref", "dict"):
                where = self._locate(form, "", "Resources")
        return form

    def set_form_content(self, names, data):
        """Replace a form's content in the in-memory copy, wherever it is used."""
        form = self.form_xref(names)
        self.doc.xref_set_key(form, "DecodeParms", "null")
        self.doc.update_stream(form, data)

    def render(self, parts, clip_pdf, dpi):
        doc = self.doc
        self.set_page_content(parts[0])
        for (holder, form), data in zip(self.forms, parts[1:]):
            doc.update_object(holder, doc.xref_object(form))  # same box, matrix, group and resources
            doc.xref_set_key(holder, "Filter", "null")
            doc.xref_set_key(holder, "DecodeParms", "null")
            doc.update_stream(holder, data, compress=False)
        mu_page = doc.load_page(self.pno)  # loaded fresh each time; no page object is kept
        to_mu = device_matrix(mu_page)
        clip = (pymupdf.Rect(clip_pdf) * to_mu).normalize()
        pix = mu_page.get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, clip=clip, alpha=False, annots=False)
        if pix.n != 4:
            raise ValueError("renderer did not return CMYK")
        data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4).astype(np.int16)
        return data, pix, to_mu
