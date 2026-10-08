"""Read-only evidence gathering for one app, used by humans and by the investigation agent.

This is the agent's main tool. It deliberately returns raw signals per storefront
rather than a verdict: the deterministic verdict already exists in the run that
raised the anomaly, and the agent's job is to look past it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .store_client import StoreClient, page_url


def probe(app_id: str, countries: list[str], client: StoreClient) -> dict:
    storefronts = {}
    for cc in countries:
        lk = client.lookup(app_id, cc)
        pg = client.page(app_id, cc)
        storefronts[cc] = {
            "lookup_found": lk.found, "lookup_error": lk.error, "metadata": lk.meta,
            "page_url": page_url(app_id, cc), "page_http": pg.http,
            "page_present": pg.present, "page_error": pg.error,
        }
    return {"app_id": app_id,
            "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "storefronts": storefronts}
