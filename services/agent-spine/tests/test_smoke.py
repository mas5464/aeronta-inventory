def test_upstream_deps_importable() -> None:
    from trax_io_feature_store import TenantContext  # noqa: F401
    from trax_io_reco.service import RecommendationService  # noqa: F401

    import trax_io_spine  # noqa: F401

    assert trax_io_spine.__version__ == "0.1.0"
