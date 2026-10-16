"""Where the models run: the ``device`` setting resolved to the hardware present."""

import torch


def resolve_device(device: str) -> str:
    """Map ``auto`` to ``cuda`` when a GPU is available, otherwise ``cpu``.

    Args:
        device: ``auto``, ``cuda`` or ``cpu`` (``ANONPIP_DEVICE``).

    Returns:
        ``cuda`` or ``cpu``, or ``device`` itself when it is not ``auto``.
    """
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device
