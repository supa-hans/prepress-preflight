"""Annotations, layers, security and output intent."""
import pikepdf

from prepress_preflight.engine import run
from prepress_preflight.model import NOTE, WARN

K_BOX = b"0 0 0 1 k 100 100 50 50 re f"


def found(result, check, severity):
    return [f for f in result.findings if f.check == check and f.severity == severity]


def amend(path, change, **save):
    """Rewrite a synthetic test PDF after changing it with `change(pdf)`."""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        change(pdf)
        pdf.save(path, **save)
    return path


def annotate(subtype, flags):
    def change(pdf):
        annot = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name("/" + subtype), Rect=[100, 100, 150, 150], F=flags)
        pdf.pages[0].obj["/Annots"] = pikepdf.Array([pdf.make_indirect(annot)])
    return change


# --- annotations
def test_stamp_set_to_print_warns(make):
    f = found(run(amend(make(K_BOX), annotate("Stamp", 4))), "Annotations", WARN)
    assert f and "Stamp" in f[0].message


def test_comment_not_set_to_print_is_only_noted(make):
    result = run(amend(make(K_BOX), annotate("Text", 0)))
    assert found(result, "Annotations", NOTE) and not found(result, "Annotations", WARN)
    assert result.status == "PASS"


def test_form_field_warns(make):
    f = found(run(amend(make(K_BOX), annotate("Widget", 4))), "Annotations", WARN)
    assert f and "form field" in f[0].message


def test_links_and_hidden_annotations_are_ignored(make):
    assert not [f for f in run(amend(make(K_BOX), annotate("Link", 4))).findings if f.check == "Annotations"]
    assert not [f for f in run(amend(make(K_BOX), annotate("Stamp", 4 | 2))).findings if f.check == "Annotations"]


# --- layers
def layers(hidden=(), base_state=None):
    def change(pdf):
        groups = {n: pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=n)) for n in ("Artwork", "Dieline")}
        config = pikepdf.Dictionary(OFF=pikepdf.Array([groups[n] for n in hidden]))
        if base_state:
            config["/BaseState"] = pikepdf.Name("/" + base_state)
            config["/ON"] = pikepdf.Array([groups["Artwork"]])
        pdf.Root["/OCProperties"] = pikepdf.Dictionary(OCGs=pikepdf.Array(list(groups.values())), D=config)
    return change


def test_hidden_layer_warns_and_names_it(make):
    f = found(run(amend(make(K_BOX), layers(hidden=["Dieline"]))), "Layers", WARN)
    assert f and "Dieline" in f[0].message and "Artwork" not in f[0].message


def test_visible_layers_are_only_listed(make):
    result = run(amend(make(K_BOX), layers()))
    assert found(result, "Layers", NOTE) and not found(result, "Layers", WARN)


def test_layers_off_by_default_state_are_hidden(make):
    f = found(run(amend(make(K_BOX), layers(base_state="OFF"))), "Layers", WARN)
    assert f and "Dieline" in f[0].message and "Artwork" not in f[0].message


def test_file_without_layers_says_nothing(make):
    assert not [f for f in run(make(K_BOX)).findings if f.check == "Layers"]


# --- security
def test_encrypted_file_warns(make):
    path = amend(make(K_BOX), lambda pdf: None, encryption=pikepdf.Encryption(owner="owner", user=""))
    assert found(run(path), "Security", WARN)


def test_plain_file_has_no_security_finding(make):
    assert not [f for f in run(make(K_BOX)).findings if f.check == "Security"]


# --- output intent
def test_output_intent_is_reported(make):
    def change(pdf):
        intent = pikepdf.Dictionary(Type=pikepdf.Name.OutputIntent, S=pikepdf.Name.GTS_PDFX,
                                    OutputConditionIdentifier="FOGRA39", Info="Coated FOGRA39")
        pdf.Root["/OutputIntents"] = pikepdf.Array([intent])
    f = found(run(amend(make(K_BOX), change)), "Output intent", NOTE)
    assert f and "Coated FOGRA39 (FOGRA39)" in f[0].message and "no profile embedded" in f[0].message


def test_missing_output_intent_is_noted_not_failed(make):
    result = run(make(K_BOX))
    f = found(result, "Output intent", NOTE)
    assert f and "No output intent" in f[0].message and result.status == "PASS"
