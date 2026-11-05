"""Removed: the legacy ``pipeline`` package.

The previous scaffold's ``pipeline`` package had unsafe lock and provenance
semantics (a blocked job deleted the holder's lock; a model-supplied source ID
could become authoritative). It has been replaced by ``blueprint_pipeline``
and the ``blueprint`` CLI rather than wrapped, so that those behaviours cannot
be carried forward silently. See docs/MIGRATION.md for the mapping.
"""

raise ImportError(
    "The legacy 'pipeline' package was removed. Install this repository "
    "('pip install -e .') and use 'blueprint_pipeline' / the 'blueprint' CLI. "
    "See docs/MIGRATION.md for the old-to-new mapping."
)
