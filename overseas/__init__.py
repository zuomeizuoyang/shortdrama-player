"""Official website adapters with one five-method interface."""
from importlib import import_module

ADAPTERS = {
    "dramabox": ("dramabox", "DramaBoxAdapter"),
    "dramawave": ("dramawave", "DramaWaveAdapter"),
    "netshort": ("netshort", "NetShortAdapter"),
    "goodshort": ("goodshort", "GoodShortAdapter"),
}


def create_adapter(platform, **kwargs):
    try:
        module, name = ADAPTERS[platform.lower()]
    except (KeyError, AttributeError) as exc:
        raise ValueError("Platform must be dramabox, dramawave, netshort or goodshort") from exc
    return getattr(import_module("." + module, __name__), name)(**kwargs)


__all__ = ["create_adapter", "ADAPTERS"]
