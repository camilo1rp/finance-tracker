"""HTTP helpers for paged transaction and summary responses."""


def listed(response):
    assert response.status_code == 200, response.text
    return response.json()["transactions"]


def details(client, **params):
    rows = listed(client.get("/transactions", params=params))
    out = []
    for row in rows:
        detail = client.get(f"/transactions/{row['id']}")
        assert detail.status_code == 200, detail.text
        out.append(detail.json())
    return out


def groups(response):
    assert response.status_code == 200, response.text
    return response.json()["groups"]
