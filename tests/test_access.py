"""Roles and the subject picker, through the real app: viewer is read-only, editor cannot reach admin
pages, deactivation ends a session, the last admin cannot be demoted, subject search ranks sensibly."""
import os, sys, tempfile
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["MOGO_DATA_DIR"] = tmp
os.environ["SECRET_KEY"] = "test"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal, init_db
from app.services import load_subject_file

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_onix30.xml"
THEMA = b'{"Code":[{"CodeValue":"W","CodeDescription":"Lifestyle"},{"CodeValue":"WS","CodeDescription":"Sports"},' \
        b'{"CodeValue":"WSZG","CodeDescription":"Walking, hiking, trekking"},{"CodeValue":"1DDU","CodeDescription":"United Kingdom"}]}'


def client_for(username, password):
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": username, "password": password, "next": "/"})
    assert r.status_code == 303 and r.headers["location"] == "/", (username, r.status_code, r.headers.get("location"))
    return c


def main():
    init_db()
    admin = TestClient(app, follow_redirects=False)
    assert admin.get("/titles").headers["location"] == "/setup"
    r = admin.post("/setup", data={"sender_name": "Test Press", "username": "admin", "password": "admin-password", "currency": "GBP",
                                   "default_country": "GB", "default_language": "eng", "tax_book_rate": "0", "tax_book_code": "Z", "tax_standard_rate": "20"})
    assert r.status_code == 303
    with open(SAMPLE, "rb") as fh:
        assert admin.post("/import", files={"file": ("s.xml", fh, "application/xml")}).status_code == 303
    for name, role in (("vera", "viewer"), ("ed", "editor")):
        assert admin.post("/users", data={"username": name, "password": "password123", "role": role}).status_code == 303

    viewer, editor = client_for("vera", "password123"), client_for("ed", "password123")
    assert viewer.get("/titles").status_code == 200
    before = admin.get("/api/products/9780000000002").json()["rrp_gbp"]
    viewer.post("/titles/1/edit", data={"rrp": "99.00", "tab": "overview"})
    assert admin.get("/api/products/9780000000002").json()["rrp_gbp"] == before, "viewer must not be able to save"
    assert viewer.get("/settings").status_code == 303 and editor.get("/settings").status_code == 303
    assert editor.get("/users").status_code == 303 and editor.post("/users", data={"username": "x", "password": "password123"}).status_code == 303
    assert admin.get("/users").text.count("<tr>") >= 3 and "x</span>" not in admin.get("/users").text

    editor.post("/titles/1/edit", data={"rrp": "12.34", "tab": "overview"})
    assert admin.get("/api/products/9780000000002").json()["rrp_gbp"] == 12.34
    assert ">ed<" in admin.get("/titles/1?tab=history").text, "history records who made the change"

    # last admin cannot be demoted or deactivated
    admin.post("/users/1", data={"role": "editor"}); assert admin.get("/users").status_code == 200
    admin.post("/users/1", data={"action": "deactivate"}); assert admin.get("/users").status_code == 200

    # deactivation ends the editor's session at once
    admin.post("/users/3", data={"action": "deactivate"})
    assert editor.get("/titles").headers["location"] == "/login"

    # subject picker
    with SessionLocal() as db:
        load_subject_file(db, "93", THEMA)
    rows = admin.get("/api/subjects/search", params={"scheme": "93", "q": "walking"}).json()
    assert rows and rows[0]["code"] == "WSZG" and rows[0]["path"] == "Lifestyle / Sports" and rows[0]["uses"] >= 1
    assert [r["code"] for r in admin.get("/api/subjects/search", params={"scheme": "93", "q": "ws"}).json()] == ["WS", "WSZG"]
    assert admin.get("/api/subjects/search", params={"scheme": "93", "q": "1D"}).json()[0]["scheme"] == "94"
    admin.post("/titles/1/subjects", data={"scheme": "93", "code": "1ddu", "heading": ""})
    assert "United Kingdom" in admin.get("/titles/1?tab=subjects").text
    assert "<SubjectSchemeIdentifier>94</SubjectSchemeIdentifier>" in admin.get("/titles/1/onix.xml").text
    print("ACCESS OK")


if __name__ == "__main__":
    main()
