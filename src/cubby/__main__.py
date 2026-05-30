"""Allow ``python -m cubby`` to behave exactly like the ``cubby`` script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
