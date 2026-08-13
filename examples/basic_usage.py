"""Minimal end-to-end example for the Langsys Python SDK.

Run against your project::

    export LANGSYS_API_KEY=… LANGSYS_PROJECT_ID=… LANGSYS_API_URL=…
    python examples/basic_usage.py
"""

from __future__ import annotations

import datetime

from langsys import LangsysClient


def main() -> None:
    client = LangsysClient()  # reads LANGSYS_* from the environment
    client.set_locale("es-ES")

    # Plain translation — the phrase is the key and the base-language default.
    print(client.translate("Technical Support", category="CAT_3"))

    # Interpolation with locale-aware formatting + ICU plurals.
    print(
        client.translate(
            "Hello, {name}! You have {count, plural, one {# new message} other {# new messages}}.",
            category="Greetings",
            params={"name": "Sarah", "count": 3},
        )
    )
    print(
        client.translate(
            "Published {when}", category="News", params={"when": datetime.date(2026, 7, 7)}
        )
    )

    # Reference data + language detection.
    print(client.country_name("DE"), "·", client.currency_name("USD"))
    print(client.detect_preferred_locale("fr,es;q=0.8", ["en-US", "es-ES", "es-CR"]))

    client.close()


if __name__ == "__main__":
    main()
