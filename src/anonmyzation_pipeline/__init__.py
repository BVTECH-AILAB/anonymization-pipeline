"""AGENAS MIA platform service."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("anonmyzation-pipeline")
except PackageNotFoundError:
    __version__ = "0.0.0"
