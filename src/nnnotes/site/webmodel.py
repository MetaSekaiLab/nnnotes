"""Catalog queries for Live2D model subjects."""

from ..content.live2d import LIVE2D_PREFIX, model_keys, select_models

__all__ = ["catalog_models"]


def catalog_models(cat, specs=None) -> dict[str, str]:
    """Selected model ids and keys from a catalog; omitted specs select every model."""
    return select_models(specs, model_keys(cat.keys(LIVE2D_PREFIX)))
