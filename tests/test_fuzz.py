"""The engine must classify any input total-function style: never raise, always
return a known category. We drive it with deterministic pseudo-random names so
failures are reproducible."""

import random
import string

from cubby.adapters.config import load_config
from cubby.domain.engine import Engine
from cubby.domain.file_ref import FileRef, Stage


def _random_name(rng: random.Random) -> str:
    length = rng.randint(0, 40)
    alphabet = string.ascii_letters + string.digits + " -_.()éàç#@"
    body = "".join(rng.choice(alphabet) for _ in range(length))
    ext = rng.choice(["", ".pdf", ".PNG", ".weirdext", ".dmg", ".txt", ".", ".tar.gz"])
    return body + ext


def test_engine_is_total_over_random_names():
    config = load_config(user_path=None)
    engine = Engine(config)
    known = set(config.managed_dirs)
    rng = random.Random(1234)

    for _ in range(5000):
        name = _random_name(rng)
        stem, _, ext = name.rpartition(".")
        ref = FileRef(
            name=name,
            stem=stem or name,
            ext=ext.lower() if stem else "",
            is_file=True,
            read_text=lambda: "",
        )
        decision = engine.classify(ref)
        assert decision.category in known
        assert isinstance(decision.stage, Stage)
