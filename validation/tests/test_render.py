"""PDF rendering of resolved specs."""

from anonval.render import render_pdf
from anonval.spec import Spec


def test_signature_without_place_date_inside_columns() -> None:
    spec = Spec.model_validate(
        {
            "id": "referto_esterno_facile_01",
            "type": "referto_esterno",
            "difficulty": "facile",
            "letterhead": {"lines": ["Studio medico"]},
            "blocks": [
                {
                    "type": "columns",
                    "left": [{"type": "signature", "name": "Dott. Ugo Riva"}],
                    "right": [
                        {"type": "signature", "role": "Il paziente", "digital": True}
                    ],
                }
            ],
        }
    )
    assert render_pdf(spec).startswith(b"%PDF-")
