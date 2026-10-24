import io
import time

import pytest

from bugmail.verifier import Verifier, MXInfo
from bugmail.web import create_app


@pytest.fixture
def client():
    v = Verifier(smtp=False, resolve_mx=lambda d: MXInfo("ok", ("mx." + d,)) if d.endswith(".test") else MXInfo("nxdomain"),
                 min_interval=0)
    return create_app(v, workers=2).test_client()


def wait_done(client, jid):
    for _ in range(100):
        body = client.get(f"/api/jobs/{jid}").get_json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_index_renders(client):
    assert b"Bug" in client.get("/").data


def test_single_check(client):
    body = client.post("/api/check", json={"email": "ana@ejemplo.test"}).get_json()
    assert (body["status"], body["reason"]) == ("valid", "domain_only")
    assert client.post("/api/check", json={"email": ""}).status_code == 400
    assert client.post("/api/check", json={"email": 5}).status_code == 400


def test_job_from_pasted_text_then_csv(client):
    text = "ana@ejemplo.test, ANA@ejemplo.test; luis@ejemplo.test\nmal@noexiste.com"
    started = client.post("/api/jobs", data={"text": text})
    assert started.status_code == 202 and started.get_json()["total"] == 3
    done = wait_done(client, started.get_json()["id"])
    assert done["counts"] == {"valid": 2, "risky": 0, "unknown": 0, "invalid": 1}
    csv_body = client.get(f"/api/jobs/{started.get_json()['id']}/csv")
    assert csv_body.mimetype == "text/csv" and b"bugmail_status" in csv_body.data


def test_job_from_uploaded_csv_keeps_original_columns(client):
    data = {"file": (io.BytesIO(b"nombre;correo\nAna;ana@ejemplo.test\n"), "lista.csv")}
    started = client.post("/api/jobs", data=data, content_type="multipart/form-data")
    jid = started.get_json()["id"]
    wait_done(client, jid)
    text = client.get(f"/api/jobs/{jid}/csv").data.decode("utf-8-sig")
    assert text.splitlines()[0] == "nombre,correo,bugmail_status,bugmail_reason,bugmail_suggestion"


def test_errors(client):
    assert client.post("/api/jobs", data={"text": ""}).status_code == 400
    assert client.post("/api/jobs", data={"text": "sin direcciones aqui"}).status_code == 400
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/csv").status_code == 404


def test_too_many_addresses_rejected(client, monkeypatch):
    monkeypatch.setattr("bugmail.web.MAX_ADDRESSES", 2)
    resp = client.post("/api/jobs", data={"text": "a@ejemplo.test b@ejemplo.test c@ejemplo.test"})
    assert resp.status_code == 413
