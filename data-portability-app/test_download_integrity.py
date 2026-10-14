"""End-to-end checks that every kind of edit — and combinations of them — is
faithfully reflected in the file the user downloads, while the structure and the
untouched conversations stay intact.

The edits under test:
  * redaction of a flagged run                 (/api/redact)
  * deletion of an arbitrary text selection    (/api/delete_text)
  * deletion of whole messages                 (/api/delete_messages)
  * undo / redo                                (/api/undo, /api/redo)
crossed with both build modes (export a subset / remove a subset).
"""

import copy
import io
import json

import app as flask_app

SAMPLE = "sample_data/anthropic_conversations.json"
ORIGINAL = json.load(open(SAMPLE))
BY_UUID = {c["uuid"]: c for c in ORIGINAL}

# Named conversations, in file order, as the table sees them.
NAMED = [c for c in ORIGINAL if c["name"] != ""]

# Handy uuids from the fixture.
DEPLOY = "3f2b9c10-9999-4a01-9c11-aa0000000009"
SOURDOUGH = "3f2b9c10-1111-4a01-9c11-aa0000000001"
NAMING = "3f2b9c10-8888-4a01-9c11-aa0000000008"
TRIP = "3f2b9c10-3333-4a01-9c11-aa0000000003"
PANDAS = "3f2b9c10-4444-4a01-9c11-aa0000000004"
BILLING = "3f2b9c10-2222-4a01-9c11-aa0000000002"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def client():
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def upload(c):
    with open(SAMPLE, "rb") as f:
        payload = f.read()
    res = c.post(
        "/api/upload",
        data={"platform": "claude", "file": (io.BytesIO(payload), "conversations.json")},
        content_type="multipart/form-data",
    )
    data = res.get_json()
    return data["token"], data["conversations"]


def index_of(rows, uuid):
    return next(r["index"] for r in rows if r["uuid"] == uuid)


def messages(c, token, rows, uuid):
    idx = index_of(rows, uuid)
    return c.get(f"/api/transcript?token={token}&index={idx}").get_json()["messages"]


def redact(c, token, cuuid, muuid, text):
    r = c.post("/api/redact", json={
        "token": token, "conversation_uuid": cuuid, "message_uuid": muuid, "text": text,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def delete_text(c, token, cuuid, muuid, text, occurrence=0):
    r = c.post("/api/delete_text", json={
        "token": token, "conversation_uuid": cuuid, "message_uuid": muuid,
        "text": text, "occurrence": occurrence,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def delete_messages(c, token, cuuid, muuids):
    r = c.post("/api/delete_messages", json={
        "token": token, "conversation_uuid": cuuid, "message_uuids": muuids,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def undo(c, token, cuuid):
    r = c.post("/api/undo", json={"token": token, "conversation_uuid": cuuid})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def redo(c, token, cuuid):
    r = c.post("/api/redo", json={"token": token, "conversation_uuid": cuuid})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def build_download(c, token, mode, uuids):
    b = c.post("/api/build", json={"token": token, "mode": mode, "uuids": uuids})
    assert b.status_code == 200, b.get_json()
    out = json.loads(c.get(f"/api/download?token={token}").data)
    return out


def conv(out, uuid):
    return next((cnv for cnv in out if cnv["uuid"] == uuid), None)


def messages_blob(cnv):
    """JSON of just the messages — excludes the title, which we never edit."""
    return json.dumps(cnv["chat_messages"])


CONV_KEYS = {"uuid", "name", "created_at", "updated_at", "account", "chat_messages"}


def assert_structural(out):
    """The download is a list of conversations with the exact original key set."""
    assert isinstance(out, list)
    for cnv in out:
        assert set(cnv) == CONV_KEYS, cnv.get("uuid")
        assert isinstance(cnv["chat_messages"], list)


# --------------------------------------------------------------------------
# Combination tests
# --------------------------------------------------------------------------

def test_all_three_edit_types_across_conversations_reach_an_export():
    c = client()
    token, rows = upload(c)

    # A redaction in one conversation…
    deploy_msgs = messages(c, token, rows, DEPLOY)
    redact(c, token, DEPLOY, deploy_msgs[0]["uuid"], "AKIAIOSFODNN7EXAMPLE")

    # …a whole-message deletion in another…
    sour_msgs = messages(c, token, rows, SOURDOUGH)
    dropped_msg = sour_msgs[1]["uuid"]
    delete_messages(c, token, SOURDOUGH, [dropped_msg])

    # …and an arbitrary text deletion in a third.
    naming_msgs = messages(c, token, rows, NAMING)
    delete_text(c, token, NAMING, naming_msgs[0]["uuid"], "side project")

    out = build_download(c, token, "export", [DEPLOY, SOURDOUGH, NAMING])
    assert_structural(out)
    assert {cnv["uuid"] for cnv in out} == {DEPLOY, SOURDOUGH, NAMING}

    # Redaction reflected.
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(conv(out, DEPLOY))
    # Message deletion reflected.
    sour = conv(out, SOURDOUGH)
    assert dropped_msg not in [m["uuid"] for m in sour["chat_messages"]]
    assert len(sour["chat_messages"]) == 3
    # Text deletion reflected in text *and* the mirroring content blocks.
    naming = conv(out, NAMING)
    assert "side project" not in messages_blob(naming)


def test_stacked_edits_on_one_conversation_all_reach_the_download():
    c = client()
    token, rows = upload(c)
    msgs = messages(c, token, rows, DEPLOY)
    user_msg, assistant_msg = msgs[0]["uuid"], msgs[1]["uuid"]

    redact(c, token, DEPLOY, user_msg, "AKIAIOSFODNN7EXAMPLE")   # flagged run
    delete_text(c, token, DEPLOY, user_msg, "staging box")        # arbitrary text
    delete_messages(c, token, DEPLOY, [assistant_msg])            # whole message

    out = build_download(c, token, "export", [DEPLOY])
    assert_structural(out)
    deploy = conv(out, DEPLOY)

    # The assistant message is gone; only the (edited) user message remains.
    assert [m["uuid"] for m in deploy["chat_messages"]] == [user_msg]
    kept = deploy["chat_messages"][0]
    assert "AKIAIOSFODNN7EXAMPLE" not in kept["text"]
    assert "staging box" not in kept["text"]
    # Untouched parts of the same message survive.
    assert "The deploy dies when it hits the" in kept["text"]
    assert "192.168.4.31" in kept["text"]
    # And nothing removed lingers anywhere in the conversation (content blocks too).
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(deploy)
    assert "staging box" not in json.dumps(deploy)


def test_edits_ride_along_in_remove_mode():
    c = client()
    token, rows = upload(c)
    naming_msgs = messages(c, token, rows, NAMING)
    delete_text(c, token, NAMING, naming_msgs[0]["uuid"], "side project")

    # Remove a *different* conversation; the edit must survive the filter.
    out = build_download(c, token, "remove", [TRIP])
    assert_structural(out)

    assert conv(out, TRIP) is None                        # removed
    assert "side project" not in messages_blob(conv(out, NAMING))   # edit kept
    # Everything else that wasn't removed is still there.
    remaining = {cnv["uuid"] for cnv in out}
    assert remaining == {c["uuid"] for c in ORIGINAL if c["uuid"] != TRIP}


def test_undone_edit_is_absent_from_the_download():
    c = client()
    token, rows = upload(c)
    msgs = messages(c, token, rows, DEPLOY)
    assistant_msg = msgs[1]["uuid"]

    delete_messages(c, token, DEPLOY, [assistant_msg])
    undo(c, token, DEPLOY)   # take it back before building

    out = build_download(c, token, "export", [DEPLOY])
    deploy = conv(out, DEPLOY)
    # The message is back, so the download matches the original conversation.
    assert [m["uuid"] for m in deploy["chat_messages"]] == \
        [m["uuid"] for m in BY_UUID[DEPLOY]["chat_messages"]]
    assert deploy == BY_UUID[DEPLOY]


def test_redone_edit_reappears_in_the_download():
    c = client()
    token, rows = upload(c)
    msgs = messages(c, token, rows, DEPLOY)
    assistant_msg = msgs[1]["uuid"]

    delete_messages(c, token, DEPLOY, [assistant_msg])
    undo(c, token, DEPLOY)
    redo(c, token, DEPLOY)   # reinstate the deletion

    out = build_download(c, token, "export", [DEPLOY])
    deploy = conv(out, DEPLOY)
    assert assistant_msg not in [m["uuid"] for m in deploy["chat_messages"]]
    assert len(deploy["chat_messages"]) == 1


def test_partial_undo_leaves_earlier_edits_in_the_download():
    """Two stacked edits, undo only the last — the first must still land."""
    c = client()
    token, rows = upload(c)
    msgs = messages(c, token, rows, DEPLOY)
    user_msg, assistant_msg = msgs[0]["uuid"], msgs[1]["uuid"]

    redact(c, token, DEPLOY, user_msg, "AKIAIOSFODNN7EXAMPLE")  # edit 1
    delete_messages(c, token, DEPLOY, [assistant_msg])          # edit 2
    undo(c, token, DEPLOY)                                      # undo edit 2 only

    out = build_download(c, token, "export", [DEPLOY])
    deploy = conv(out, DEPLOY)
    # The assistant message is back…
    assert assistant_msg in [m["uuid"] for m in deploy["chat_messages"]]
    assert len(deploy["chat_messages"]) == 2
    # …but the earlier redaction is still applied.
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(deploy)


def test_untouched_conversations_are_byte_identical_after_edits_elsewhere():
    c = client()
    token, rows = upload(c)
    deploy_msgs = messages(c, token, rows, DEPLOY)
    redact(c, token, DEPLOY, deploy_msgs[0]["uuid"], "AKIAIOSFODNN7EXAMPLE")
    naming_msgs = messages(c, token, rows, NAMING)
    delete_text(c, token, NAMING, naming_msgs[0]["uuid"], "side project")

    # Export the whole file.
    out = build_download(c, token, "export", [c["uuid"] for c in NAMED])
    assert_structural(out)

    # Conversations we didn't touch come out exactly as they went in.
    for untouched in (PANDAS, BILLING, TRIP):
        assert conv(out, untouched) == BY_UUID[untouched]

    # The edited ones differ only by the removed text.
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(conv(out, DEPLOY))
    assert "side project" not in messages_blob(conv(out, NAMING))


def test_deleting_every_message_plus_edits_elsewhere_in_one_download():
    c = client()
    token, rows = upload(c)
    deploy_msgs = messages(c, token, rows, DEPLOY)
    delete_messages(c, token, DEPLOY, [m["uuid"] for m in deploy_msgs])  # empty it
    naming_msgs = messages(c, token, rows, NAMING)
    delete_text(c, token, NAMING, naming_msgs[0]["uuid"], "side project")

    out = build_download(c, token, "export", [DEPLOY, NAMING])
    assert_structural(out)

    deploy = conv(out, DEPLOY)
    assert deploy["chat_messages"] == []          # present but emptied
    assert set(deploy) == CONV_KEYS               # structure intact
    assert "side project" not in messages_blob(conv(out, NAMING))


def test_rebuild_after_more_edits_reflects_the_latest_state():
    c = client()
    token, rows = upload(c)
    msgs = messages(c, token, rows, DEPLOY)
    user_msg = msgs[0]["uuid"]

    redact(c, token, DEPLOY, user_msg, "AKIAIOSFODNN7EXAMPLE")
    first = build_download(c, token, "export", [DEPLOY])
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(first)
    assert "192.168.4.31" in json.dumps(first)     # not yet edited

    # A further edit, then a fresh build — the new file has both changes.
    delete_text(c, token, DEPLOY, user_msg, "192.168.4.31")
    second = build_download(c, token, "export", [DEPLOY])
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(second)
    assert "192.168.4.31" not in json.dumps(second)


def test_edits_never_touch_the_uploaded_source_file():
    """All edits go to the session working copy; the user's file is untouched."""
    before = copy.deepcopy(ORIGINAL)
    c = client()
    token, rows = upload(c)

    deploy_msgs = messages(c, token, rows, DEPLOY)
    redact(c, token, DEPLOY, deploy_msgs[0]["uuid"], "AKIAIOSFODNN7EXAMPLE")
    delete_messages(c, token, SOURDOUGH,
                    [messages(c, token, rows, SOURDOUGH)[1]["uuid"]])
    build_download(c, token, "export", [DEPLOY, SOURDOUGH])

    assert json.load(open(SAMPLE)) == before


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
