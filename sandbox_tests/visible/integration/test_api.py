from fastapi.testclient import TestClient


def test_login_rejects_bad_password(client: TestClient) -> None:
    r = client.post("/auth/login", json={"email": "user1@tenant1.sim", "password": "nope"})
    assert r.status_code == 401


def test_requests_without_token_are_rejected(client: TestClient) -> None:
    assert client.get("/projects").status_code == 401


def test_tenant_isolation(client: TestClient, auth: dict[str, str]) -> None:
    projects = client.get("/projects", headers=auth).json()
    assert {p["id"] for p in projects} == {1, 2}
    assert client.get("/projects/3/tasks", headers=auth).status_code == 404


def test_task_create_update_get(client: TestClient, auth: dict[str, str]) -> None:
    created = client.post("/projects/1/tasks", json={"title": "t"}, headers=auth).json()
    r = client.patch(f"/tasks/{created['id']}", json={"status": "done"}, headers=auth)
    assert r.json()["status"] == "done"
    assert client.get(f"/tasks/{created['id']}", headers=auth).json()["status"] == "done"
