"""Phase 2 Collaboration feature tests — Workspaces, Notifications, Activity Feed, Comment Reactions."""
import pytest
from starlette.testclient import TestClient

from teb.main import app, reset_rate_limits


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _register(client, email="user1@test.com", password="TestPass123!"):
    reset_rate_limits()
    resp = client.post("/api/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    return resp.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Workspace Tests ────────────────────────────────────────────────────────


class TestWorkspaceCreation:
    def test_create_workspace(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={"name": "My Team"}, headers=_auth(token))
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Team"
        assert data["owner_id"] is not None
        assert data["invite_code"] != ""
        assert data["plan"] == "free"
        assert data["id"] is not None

    def test_create_workspace_with_description(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={
            "name": "Dev Team", "description": "Engineering workspace", "plan": "pro",
        }, headers=_auth(token))
        assert resp.status_code == 201
        data = resp.json()
        assert data["description"] == "Engineering workspace"
        assert data["plan"] == "pro"

    def test_create_workspace_empty_name_fails(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={"name": ""}, headers=_auth(token))
        assert resp.status_code == 422

    def test_create_workspace_no_auth_fails(self, client):
        resp = client.post("/api/workspaces", json={"name": "Test"})
        assert resp.status_code == 401


class TestWorkspaceListing:
    def test_list_workspaces(self, client):
        token = _register(client)
        client.post("/api/workspaces", json={"name": "WS1"}, headers=_auth(token))
        client.post("/api/workspaces", json={"name": "WS2"}, headers=_auth(token))
        resp = client.get("/api/workspaces", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_list_workspaces_empty(self, client):
        token = _register(client)
        resp = client.get("/api/workspaces", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_workspace_by_id(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={"name": "My WS"}, headers=_auth(token))
        ws_id = resp.json()["id"]
        resp = client.get(f"/api/workspaces/{ws_id}", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["name"] == "My WS"

    def test_get_workspace_not_found(self, client):
        token = _register(client)
        resp = client.get("/api/workspaces/9999", headers=_auth(token))
        assert resp.status_code == 404

    def test_get_workspace_non_member_forbidden(self, client):
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "other@test.com")
        resp = client.post("/api/workspaces", json={"name": "Private"}, headers=_auth(token1))
        ws_id = resp.json()["id"]
        resp = client.get(f"/api/workspaces/{ws_id}", headers=_auth(token2))
        assert resp.status_code == 403


class TestWorkspaceJoinInvite:
    def test_join_by_invite_code(self, client):
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "joiner@test.com")
        resp = client.post("/api/workspaces", json={"name": "Open Team"}, headers=_auth(token1))
        invite_code = resp.json()["invite_code"]
        resp = client.post("/api/workspaces/join", json={"invite_code": invite_code}, headers=_auth(token2))
        assert resp.status_code == 200
        assert resp.json()["role"] == "member"

    def test_join_invalid_code(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces/join", json={"invite_code": "bad-code"}, headers=_auth(token))
        assert resp.status_code == 404

    def test_join_already_member(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={"name": "WS"}, headers=_auth(token))
        invite_code = resp.json()["invite_code"]
        resp = client.post("/api/workspaces/join", json={"invite_code": invite_code}, headers=_auth(token))
        assert resp.status_code == 409

    def test_join_empty_code_fails(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces/join", json={"invite_code": ""}, headers=_auth(token))
        assert resp.status_code == 422


class TestWorkspaceMembers:
    def test_add_member(self, client):
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "member@test.com")
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token1))
        ws_id = resp.json()["id"]
        # Get user2 id
        resp2 = client.post("/api/auth/login", json={"email": "member@test.com", "password": "TestPass123!"})
        user2_id = resp2.json()["user"]["id"]
        resp = client.post(f"/api/workspaces/{ws_id}/members",
                           json={"user_id": user2_id, "role": "member"}, headers=_auth(token1))
        assert resp.status_code == 201
        assert resp.json()["user_id"] == user2_id

    def test_add_member_already_exists(self, client):
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "member@test.com")
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token1))
        ws_id = resp.json()["id"]
        resp2 = client.post("/api/auth/login", json={"email": "member@test.com", "password": "TestPass123!"})
        user2_id = resp2.json()["user"]["id"]
        client.post(f"/api/workspaces/{ws_id}/members",
                    json={"user_id": user2_id}, headers=_auth(token1))
        resp = client.post(f"/api/workspaces/{ws_id}/members",
                           json={"user_id": user2_id}, headers=_auth(token1))
        assert resp.status_code == 409

    def test_list_members(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token))
        ws_id = resp.json()["id"]
        resp = client.get(f"/api/workspaces/{ws_id}/members", headers=_auth(token))
        assert resp.status_code == 200
        members = resp.json()
        assert len(members) == 1  # owner auto-added
        assert members[0]["role"] == "owner"

    def test_remove_member(self, client):
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "member@test.com")
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token1))
        ws_id = resp.json()["id"]
        resp2 = client.post("/api/auth/login", json={"email": "member@test.com", "password": "TestPass123!"})
        user2_id = resp2.json()["user"]["id"]
        client.post(f"/api/workspaces/{ws_id}/members",
                    json={"user_id": user2_id}, headers=_auth(token1))
        resp = client.delete(f"/api/workspaces/{ws_id}/members/{user2_id}", headers=_auth(token1))
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_cannot_remove_owner(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token))
        ws_id = resp.json()["id"]
        owner_id = resp.json()["owner_id"]
        resp = client.delete(f"/api/workspaces/{ws_id}/members/{owner_id}", headers=_auth(token))
        assert resp.status_code == 400

    def test_non_admin_cannot_add_member(self, client):
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "member@test.com")
        token3 = _register(client, "other@test.com")
        # Create workspace
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token1))
        ws_id = resp.json()["id"]
        invite_code = resp.json()["invite_code"]
        # User2 joins as member
        client.post("/api/workspaces/join", json={"invite_code": invite_code}, headers=_auth(token2))
        # User2 (member role) tries to add user3
        resp3 = client.post("/api/auth/login", json={"email": "other@test.com", "password": "TestPass123!"})
        user3_id = resp3.json()["user"]["id"]
        resp = client.post(f"/api/workspaces/{ws_id}/members",
                           json={"user_id": user3_id}, headers=_auth(token2))
        assert resp.status_code == 403

    def test_non_member_cannot_list_members(self, client):
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "outsider@test.com")
        resp = client.post("/api/workspaces", json={"name": "Private"}, headers=_auth(token1))
        ws_id = resp.json()["id"]
        resp = client.get(f"/api/workspaces/{ws_id}/members", headers=_auth(token2))
        assert resp.status_code == 403

    def test_add_member_invalid_role(self, client):
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "member@test.com")
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token1))
        ws_id = resp.json()["id"]
        resp2 = client.post("/api/auth/login", json={"email": "member@test.com", "password": "TestPass123!"})
        user2_id = resp2.json()["user"]["id"]
        resp = client.post(f"/api/workspaces/{ws_id}/members",
                           json={"user_id": user2_id, "role": "superadmin"}, headers=_auth(token1))
        assert resp.status_code == 422

    def test_add_member_missing_user_id(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token))
        ws_id = resp.json()["id"]
        resp = client.post(f"/api/workspaces/{ws_id}/members", json={}, headers=_auth(token))
        assert resp.status_code == 422

    def test_remove_nonexistent_member(self, client):
        token = _register(client)
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token))
        ws_id = resp.json()["id"]
        resp = client.delete(f"/api/workspaces/{ws_id}/members/9999", headers=_auth(token))
        assert resp.status_code == 404


# ─── Notification Tests ─────────────────────────────────────────────────────


class TestNotifications:
    def _setup_workspace_with_notification(self, client):
        """Create workspace + add member → triggers notification for added member."""
        token1 = _register(client, "owner@test.com")
        token2 = _register(client, "member@test.com")
        resp = client.post("/api/workspaces", json={"name": "Team"}, headers=_auth(token1))
        ws_id = resp.json()["id"]
        resp2 = client.post("/api/auth/login", json={"email": "member@test.com", "password": "TestPass123!"})
        user2_id = resp2.json()["user"]["id"]
        client.post(f"/api/workspaces/{ws_id}/members",
                    json={"user_id": user2_id}, headers=_auth(token1))
        return token1, token2, ws_id, user2_id

    def test_notification_created_on_member_add(self, client):
        _, token2, _, _ = self._setup_workspace_with_notification(client)
        resp = client.get("/api/notifications", headers=_auth(token2))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert "added to workspace" in data[0]["title"]

    def test_list_notifications_empty(self, client):
        token = _register(client)
        resp = client.get("/api/notifications", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_unread_only(self, client):
        _, token2, _, _ = self._setup_workspace_with_notification(client)
        resp = client.get("/api/notifications?unread_only=true", headers=_auth(token2))
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_mark_notification_read(self, client):
        _, token2, _, _ = self._setup_workspace_with_notification(client)
        resp = client.get("/api/notifications", headers=_auth(token2))
        notif_id = resp.json()[0]["id"]
        resp = client.post(f"/api/notifications/{notif_id}/read", headers=_auth(token2))
        assert resp.status_code == 200
        assert resp.json()["read"] is True
        # Verify it's now read
        resp = client.get("/api/notifications?unread_only=true", headers=_auth(token2))
        assert all(n["id"] != notif_id for n in resp.json())

    def test_mark_notification_read_not_found(self, client):
        token = _register(client)
        resp = client.post("/api/notifications/9999/read", headers=_auth(token))
        assert resp.status_code == 404

    def test_mark_all_read(self, client):
        _, token2, _, _ = self._setup_workspace_with_notification(client)
        resp = client.post("/api/notifications/read-all", headers=_auth(token2))
        assert resp.status_code == 200
        assert resp.json()["marked"] >= 1
        # Verify all read
        resp = client.get("/api/notifications?unread_only=true", headers=_auth(token2))
        assert resp.json() == []

    def test_unread_count(self, client):
        _, token2, _, _ = self._setup_workspace_with_notification(client)
        resp = client.get("/api/notifications/count", headers=_auth(token2))
        assert resp.status_code == 200
        assert resp.json()["unread"] >= 1

    def test_unread_count_zero(self, client):
        token = _register(client)
        resp = client.get("/api/notifications/count", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["unread"] == 0

    def test_notification_with_limit(self, client):
        _, token2, _, _ = self._setup_workspace_with_notification(client)
        resp = client.get("/api/notifications?limit=1", headers=_auth(token2))
        assert resp.status_code == 200
        assert len(resp.json()) <= 1


# ─── Activity Feed Tests ────────────────────────────────────────────────────


class TestActivityFeed:
    def test_activity_created_on_workspace_creation(self, client):
        token = _register(client)
        client.post("/api/workspaces", json={"name": "My WS"}, headers=_auth(token))
        resp = client.get("/api/activity", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["entity_type"] == "workspace"
        assert data[0]["action"] == "created"

    def test_activity_feed_empty(self, client):
        token = _register(client)
        resp = client.get("/api/activity", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_activity_feed_with_limit(self, client):
        token = _register(client)
        client.post("/api/workspaces", json={"name": "WS1"}, headers=_auth(token))
        client.post("/api/workspaces", json={"name": "WS2"}, headers=_auth(token))
        resp = client.get("/api/activity?limit=1", headers=_auth(token))
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_activity_feed_filter_by_workspace(self, client):
        token = _register(client)
        r1 = client.post("/api/workspaces", json={"name": "WS1"}, headers=_auth(token))
        ws1_id = r1.json()["id"]
        client.post("/api/workspaces", json={"name": "WS2"}, headers=_auth(token))
        resp = client.get(f"/api/activity?workspace_id={ws1_id}", headers=_auth(token))
        assert resp.status_code == 200
        for entry in resp.json():
            assert entry["workspace_id"] == ws1_id

    def test_activity_no_auth(self, client):
        resp = client.get("/api/activity")
        assert resp.status_code == 401


# ─── Comment Reactions Tests ─────────────────────────────────────────────────


class TestCommentReactions:
    def _create_comment(self, client, token):
        """Helper: create a goal, task, and comment, returning the comment id."""
        resp = client.post("/api/goals", json={"title": "G1", "description": "d"}, headers=_auth(token))
        goal_id = resp.json()["id"]
        resp = client.post("/api/tasks", json={
            "goal_id": goal_id, "title": "T1", "description": "d",
        }, headers=_auth(token))
        task_id = resp.json()["id"]
        resp = client.post(f"/api/tasks/{task_id}/comments", json={
            "content": "Nice work!"
        }, headers=_auth(token))
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_add_reaction(self, client):
        token = _register(client)
        cid = self._create_comment(client, token)
        resp = client.post(f"/api/comments/{cid}/reactions", json={"emoji": "🎉"}, headers=_auth(token))
        assert resp.status_code == 201
        data = resp.json()
        assert data["emoji"] == "🎉"
        assert data["comment_id"] == cid

    def test_add_default_reaction(self, client):
        token = _register(client)
        cid = self._create_comment(client, token)
        resp = client.post(f"/api/comments/{cid}/reactions", json={}, headers=_auth(token))
        assert resp.status_code == 201
        assert resp.json()["emoji"] == "👍"

    def test_add_duplicate_reaction_fails(self, client):
        token = _register(client)
        cid = self._create_comment(client, token)
        client.post(f"/api/comments/{cid}/reactions", json={"emoji": "👍"}, headers=_auth(token))
        resp = client.post(f"/api/comments/{cid}/reactions", json={"emoji": "👍"}, headers=_auth(token))
        assert resp.status_code == 409

    def test_list_reactions(self, client):
        token = _register(client)
        cid = self._create_comment(client, token)
        client.post(f"/api/comments/{cid}/reactions", json={"emoji": "👍"}, headers=_auth(token))
        client.post(f"/api/comments/{cid}/reactions", json={"emoji": "🎉"}, headers=_auth(token))
        resp = client.get(f"/api/comments/{cid}/reactions", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_remove_reaction(self, client):
        token = _register(client)
        cid = self._create_comment(client, token)
        client.post(f"/api/comments/{cid}/reactions", json={"emoji": "👍"}, headers=_auth(token))
        resp = client.delete(f"/api/comments/{cid}/reactions/👍", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # Verify removed
        resp = client.get(f"/api/comments/{cid}/reactions", headers=_auth(token))
        assert resp.json() == []

    def test_remove_nonexistent_reaction(self, client):
        token = _register(client)
        cid = self._create_comment(client, token)
        resp = client.delete(f"/api/comments/{cid}/reactions/👍", headers=_auth(token))
        assert resp.status_code == 404

    def test_reactions_no_auth(self, client):
        resp = client.get("/api/comments/1/reactions")
        assert resp.status_code == 401

    def test_multiple_users_react(self, client):
        token1 = _register(client, "user1@test.com")
        token2 = _register(client, "user2@test.com")
        cid = self._create_comment(client, token1)
        client.post(f"/api/comments/{cid}/reactions", json={"emoji": "👍"}, headers=_auth(token1))
        client.post(f"/api/comments/{cid}/reactions", json={"emoji": "👍"}, headers=_auth(token2))
        resp = client.get(f"/api/comments/{cid}/reactions", headers=_auth(token1))
        assert len(resp.json()) == 2
