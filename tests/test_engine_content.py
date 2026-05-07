"""Stage-2 behaviour: classify by extracted text when the name is useless."""
from cubby.domain.category import Config, Settings
from cubby.domain.engine import Engine
from cubby.domain.file_ref import Stage


def test_cryptic_name_routed_by_content(sample_config, make_ref):
    engine = Engine(sample_config)
    ref = make_ref(
        "3c0fe3ad-5fe6-460a-901f-b4ece10bc25d.pdf",
        text="Statuts. Il a ete convenu de constituer une association sans but lucratif",
    )
    decision = engine.classify(ref)
    assert decision.category == "Legal"
    assert decision.stage is Stage.CONTENT


def test_content_only_used_when_name_silent(sample_config, make_ref):
    # A clear name must short-circuit before any extraction happens.
    engine = Engine(sample_config)
    called = False

    def reader() -> str:
        nonlocal called
        called = True
        return "association sans but lucratif"

    ref = make_ref("Invoice-99.pdf")
    ref.read_text = reader
    decision = engine.classify(ref)
    assert decision.category == "Invoices"
    assert called is False  # extraction never ran


def test_content_scan_can_be_disabled(sample_config, make_ref):
    cfg = Config(
        settings=Settings(content_scan=False),
        categories=sample_config.categories,
    )
    engine = Engine(cfg)
    ref = make_ref("12345678.pdf", text="numero de facture 42")
    # Falls through to type, not content.
    assert engine.classify(ref).stage is Stage.TYPE
