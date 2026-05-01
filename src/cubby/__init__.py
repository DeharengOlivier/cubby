"""Cubby - a cascade file sorter for your Downloads folder.

Architecture (dependencies point inward):

    cli  ->  app  ->  domain
              |          ^
              v          |
           adapters -----+

* ``domain`` holds pure business logic (rules, the classification cascade,
  duration parsing) and depends on nothing else in the package.
* ``adapters`` implement IO concerns (text extraction, filesystem moves,
  config loading, OS services) behind small ports the domain/app define.
* ``app`` orchestrates use cases (sort once, watch a folder).
* ``cli`` is the entry point that wires everything together.
"""

__version__ = "0.1.0"
