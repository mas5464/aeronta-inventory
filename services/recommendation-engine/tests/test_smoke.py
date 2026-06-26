from __future__ import annotations


def test_imports() -> None:
    import trax_io_reco

    assert trax_io_reco.__version__ == "0.1.0"


def test_feature_store_dependency_importable() -> None:
    # The engine reuses the feature-store schemas; confirm the path dependency resolves.
    from trax_io_feature_store.schemas import PartAttributes  # noqa: F401
