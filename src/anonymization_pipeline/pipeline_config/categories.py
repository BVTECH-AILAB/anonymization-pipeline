"""Registry of the personal data categories (``resources/categories.yaml``)."""

import functools
from dataclasses import dataclass

from anonymization_pipeline.pipeline_config.resources import read_yaml


@dataclass(frozen=True)
class Registry:
    """Groups, descriptions and generic categories.

    Attributes:
        descriptions: CATEGORY -> description, in file order.
        groups: group -> its categories.
        generic: specific category -> its generic category.
        partial: categories that hold only a part of an entity (NOME, COGNOME).
    """

    descriptions: dict[str, str]
    groups: dict[str, tuple[str, ...]]
    generic: dict[str, str]
    partial: frozenset[str] = frozenset()

    def family(self, category: str) -> str:
        """Generic category of reference: values of different families never share a tag."""
        return self.generic.get(category, category)

    def rank(self, category: str) -> int:
        """How informative a category is for a group tag: specific 2, generic 1, partial 0."""
        if category in self.partial:
            return 0
        return 1 if category in set(self.generic.values()) else 2

    def expand(self, entries: list[str]) -> list[str]:
        """Groups and categories -> categories, in registry order, without duplicates.

        Raises:
            ValueError: On an unknown group or category (the message reaches API users).
        """
        chosen: set[str] = set()
        for entry in entries:
            if entry.lower() in self.groups:
                chosen.update(self.groups[entry.lower()])
            elif entry.upper() in self.descriptions:
                chosen.add(entry.upper())
            else:
                raise ValueError(f"Gruppo o categoria sconosciuta: {entry}")
        return [c for c in self.descriptions if c in chosen]


def _build(data: dict) -> Registry:
    descriptions: dict[str, str] = {}
    groups: dict[str, tuple[str, ...]] = {}
    for group, categories in (data.get("groups") or {}).items():
        groups[str(group).lower()] = tuple(categories)
        for category, description in categories.items():
            if category in descriptions:
                raise ValueError(
                    f"categories.yaml: category {category} in more than one group"
                )
            descriptions[category] = str(description)
    generic: dict[str, str] = {}
    for general, specifics in (data.get("generic") or {}).items():
        for category in [general, *specifics]:
            if category not in descriptions:
                raise ValueError(
                    f"categories.yaml: unknown category in generic: {category}"
                )
        for category in specifics:
            generic[category] = general
    partial = frozenset(data.get("partial") or [])
    if unknown := partial - set(descriptions):
        raise ValueError(
            f"categories.yaml: unknown categories in partial: {', '.join(sorted(unknown))}"
        )
    return Registry(descriptions, groups, generic, partial)


@functools.cache
def registry() -> Registry:
    """The category registry, read once per process."""
    return _build(read_yaml("categories.yaml"))
