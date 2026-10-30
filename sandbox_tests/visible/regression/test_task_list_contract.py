from fastapi.testclient import TestClient

FIELDS = {"id", "title", "status", "created_at", "assignee", "comment_count"}


def test_task_list_shape_and_limit(client: TestClient, auth: dict[str, str]) -> None:
    rows = client.get("/projects/1/tasks", headers=auth).json()
    assert len(rows) == 50
    assert all(set(r) == FIELDS for r in rows)
    assert all(isinstance(r["comment_count"], int) for r in rows)


def test_task_list_status_filter(client: TestClient, auth: dict[str, str]) -> None:
    rows = client.get("/projects/1/tasks", params={"status": "todo"}, headers=auth).json()
    assert rows and all(r["status"] == "todo" for r in rows)


def test_task_list_is_newest_first(client: TestClient, auth: dict[str, str]) -> None:
    rows = client.get("/projects/1/tasks", headers=auth).json()
    stamps = [r["created_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)
