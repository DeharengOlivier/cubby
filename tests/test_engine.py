from cubby.domain.engine import Engine
from cubby.domain.file_ref import Stage


def test_strong_extension_beats_name(sample_config, make_ref):
    # ".dmg" is a strong extension: it wins even though "facture" is in the name.
    engine = Engine(sample_config)
    decision = engine.classify(make_ref("facture-installer.dmg"))
    assert decision.category == "Installers"
    assert decision.stage is Stage.STRONG_EXT


def test_name_stage(sample_config, make_ref):
    engine = Engine(sample_config)
    decision = engine.classify(make_ref("Invoice-2024-001.pdf"))
    assert decision.category == "Invoices"
    assert decision.stage is Stage.NAME


def test_type_fallback(sample_config, make_ref):
    engine = Engine(sample_config)
    decision = engine.classify(make_ref("random-notes.txt"))
    assert decision.category == "Documents"
    assert decision.stage is Stage.TYPE


def test_unsorted_when_nothing_matches(sample_config, make_ref):
    engine = Engine(sample_config)
    decision = engine.classify(make_ref("mystery.xyz"))
    assert decision.category == sample_config.settings.unsorted_dir
    assert decision.stage is Stage.UNSORTED


def test_config_order_wins_within_stage(sample_config, make_ref):
    # "convention" only matches Legal; ensure earlier categories do not shadow.
    engine = Engine(sample_config)
    assert engine.classify(make_ref("convention.pdf")).category == "Legal"
