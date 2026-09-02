"""End-to-end checks for the ChatGPT (OpenAI) platform: parsing the mapping
graph, the edit operations, and edits flowing through to the download.
"""

import io
import json

import app as flask_app
import openai_parser as op

SAMPLE = "sample_data/openai_conversations.json"
ORIGINAL = json.load(open(SAMPLE))


def client():
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def upload(c):
    with open(SAMPLE, "rb") as f:
        payload = f.read()
    res = c.post(
        "/api/upload",
        data={"platform": "openai", "file": (io.BytesIO(payload), "conversations.json")},
        content_type="multipart/form-data",
    )
    return res, res.get_json()


def row_named(rows, name):
    return next(r for r in rows if r["name"] == name)


def messages(c, token, index):
    return c.get(f"/api/transcript?token={token}&index={index}").get_json()["messages"]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def test_untitled_conversations_are_dropped():
    c = client()
    res, data = upload(c)
    assert res.status_code == 200
    names = [r["name"] for r in data["conversations"]]
    assert "Weekend hiking plans" in names
    assert "" not in names                    # the untitled one is filtered out
    assert len(data["conversations"]) == 4    # 5 in the file, minus the untitled


def test_transcript_filters_system_and_tool_nodes():
    c = client()
    _, data = upload(c)
    row = row_named(data["conversations"], "Weekend hiking plans")
    msgs = messages(c, data["token"], row["index"])

    # Only the user turn and the tool-authored answer survive; system + the
    # empty assistant + the `search(...)` code node are all filtered out.
    assert [m["sender"] for m in msgs] == ["human", "assistant"]
    assert "well-marked trail" in msgs[1]["text"]
    # Times come through as ISO strings the UI can format.
    assert msgs[0]["created_at"].startswith("20")


def test_branch_follows_the_current_node():
    c = client()
    _, data = upload(c)
    row = row_named(data["conversations"], "Naming a startup")
    msgs = messages(c, data["token"], row["index"])
    # current_node points at the newer answer, so that's what shows.
    assert "Quantly" in msgs[1]["text"]
    assert "DataPeak" not in msgs[1]["text"]


def test_sensitivity_covers_openai_conversations():
    c = client()
    _, data = upload(c)
    by_name = {r["name"]: r for r in data["conversations"]}
    assert by_name["Weekend hiking plans"]["sensitivity"] == "Green"
    assert by_name["Leaked AWS key in a repo"]["sensitivity"] == "Red"
    assert by_name["Dinner spots"]["sensitivity"] == "Yellow"


# --------------------------------------------------------------------------
# Editing + download
# --------------------------------------------------------------------------

def test_redaction_clears_the_tag_and_reaches_the_download():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = row_named(data["conversations"], "Leaked AWS key in a repo")
    cid = row["uuid"]
    user_msg = messages(c, token, row["index"])[0]
    assert "AKIAIOSFODNN7EXAMPLE" in user_msg["text"]

    r = c.post("/api/redact", json={
        "token": token, "conversation_uuid": cid,
        "message_uuid": user_msg["uuid"], "text": "AKIAIOSFODNN7EXAMPLE",
    })
    assert r.status_code == 200
    assert r.get_json()["row"]["sensitivity"] == "Green"

    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [cid]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(out)
    # Structure preserved — the conversation keeps its full key set.
    assert set(out[0]) == set(ORIGINAL[1])


def test_delete_text_boxes_only_the_selection():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = row_named(data["conversations"], "Dinner spots")
    cid = row["uuid"]
    user_msg = messages(c, token, row["index"])[0]

    r = c.post("/api/delete_text", json={
        "token": token, "conversation_uuid": cid,
        "message_uuid": user_msg["uuid"], "text": "near me", "occurrence": 0,
    })
    assert r.status_code == 200
    after = messages(c, token, row["index"])[0]
    # The selection is boxed (a redacted segment), the rest untouched…
    redacted = [s["text"] for s in after["segments"] if s.get("redacted")]
    assert "near me" in redacted
    assert "sushi place" in after["text"]
    # …and stripped from that message in the export (the assistant's separate
    # "sushi near me" is untouched, so scope the check to the user's node).
    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [cid]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    user_node = out[0]["mapping"][user_msg["uuid"]]
    node_text = json.dumps(user_node["message"]["content"])
    assert "near me" not in node_text
    assert "sushi place" in node_text


def test_delete_message_shrinks_the_conversation_and_survives_download():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = row_named(data["conversations"], "Weekend hiking plans")
    cid = row["uuid"]
    msgs = messages(c, token, row["index"])
    answer_node = msgs[1]["uuid"]

    r = c.post("/api/delete_messages", json={
        "token": token, "conversation_uuid": cid, "message_uuids": [answer_node],
    })
    assert r.get_json()["row"]["message_count"] == 1

    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [cid]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    # The deleted node is gone from the mapping, and current_node stays valid.
    assert answer_node not in out[0]["mapping"]
    assert out[0]["current_node"] in out[0]["mapping"]
    assert op._transcript(out[0]) == op._transcript(out[0])  # still traversable


def test_combined_edits_and_undo_redo_reach_the_download():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = row_named(data["conversations"], "Leaked AWS key in a repo")
    cid = row["uuid"]
    msgs = messages(c, token, row["index"])

    c.post("/api/redact", json={
        "token": token, "conversation_uuid": cid,
        "message_uuid": msgs[0]["uuid"], "text": "AKIAIOSFODNN7EXAMPLE",
    })
    c.post("/api/delete_messages", json={
        "token": token, "conversation_uuid": cid, "message_uuids": [msgs[1]["uuid"]],
    })
    # Undo the deletion; the redaction stays applied.
    undo = c.post("/api/undo", json={"token": token, "conversation_uuid": cid}).get_json()
    assert undo["deletions"] == 0 and undo["redactions"] == 1
    assert undo["row"]["message_count"] == 2

    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [cid]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(out)   # redaction survives
    assert len(op._transcript(out[0])) == 2                # deletion was undone


def test_remove_mode_keeps_edited_conversations():
    c = client()
    _, data = upload(c)
    token = data["token"]
    near = row_named(data["conversations"], "Dinner spots")
    hiking = row_named(data["conversations"], "Weekend hiking plans")
    user_msg = messages(c, token, near["index"])[0]

    c.post("/api/delete_text", json={
        "token": token, "conversation_uuid": near["uuid"],
        "message_uuid": user_msg["uuid"], "text": "near me", "occurrence": 0,
    })
    # Build via remove-mode, dropping a *different* conversation.
    c.post("/api/build", json={"token": token, "mode": "remove", "uuids": [hiking["uuid"]]})
    out = json.loads(c.get(f"/api/download?token={token}").data)

    ids = [conv["id"] for conv in out]
    assert hiking["uuid"] not in ids                    # removed
    assert near["uuid"] in ids                          # edit kept
    edited = next(conv for conv in out if conv["id"] == near["uuid"])
    # The edit applies to the user's message (the assistant reply still says it).
    human_text = op._transcript(edited)[0]["text"]
    assert "near me" not in human_text


def test_the_uploaded_openai_file_is_never_modified():
    before = json.dumps(ORIGINAL, sort_keys=True)
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = row_named(data["conversations"], "Leaked AWS key in a repo")
    msg = messages(c, token, row["index"])[0]
    c.post("/api/redact", json={
        "token": token, "conversation_uuid": row["uuid"],
        "message_uuid": msg["uuid"], "text": "AKIAIOSFODNN7EXAMPLE",
    })
    assert json.dumps(json.load(open(SAMPLE)), sort_keys=True) == before


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
