"""End-to-end checks for the upload -> filter -> download flow."""

import io
import json

import app as flask_app

SAMPLE = "sample_data/conversations.json"


def client():
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def upload(c, path=SAMPLE):
    with open(path, "rb") as f:
        payload = f.read()
    res = c.post(
        "/api/upload",
        data={"platform": "claude", "file": (io.BytesIO(payload), "conversations.json")},
        content_type="multipart/form-data",
    )
    return res, res.get_json()


def test_upload_lists_only_named_conversations():
    c = client()
    res, data = upload(c)
    assert res.status_code == 200
    assert data["total_in_file"] == 10         # everything in the file
    assert len(data["conversations"]) == 9     # the blank-named one is filtered out
    assert all(row["name"] for row in data["conversations"])

    first = data["conversations"][0]
    assert first["name"] == "Weekend sourdough troubleshooting"
    assert first["message_count"] == 4
    assert first["duration"] == "39m"


def test_every_row_carries_a_sensitivity_tag():
    c = client()
    _, data = upload(c)
    by_name = {row["name"]: row for row in data["conversations"]}

    for row in data["conversations"]:
        assert row["sensitivity"] in {"Green", "Yellow", "Red"}
        assert row["sensitivity_rank"] == {"Green": 0, "Yellow": 1, "Red": 2}[row["sensitivity"]]
        # Green means nothing fired; anything else has to say why.
        assert bool(row["sensitivity_reasons"]) == (row["sensitivity"] != "Green")

    assert by_name["Weekend sourdough troubleshooting"]["sensitivity"] == "Green"
    assert by_name["Trip notes: Lisbon in November"]["sensitivity"] == "Yellow"

    secrets = by_name["Deploy script keeps failing on staging"]
    assert secrets["sensitivity"] == "Red"
    assert any("secrets" in r for r in secrets["sensitivity_reasons"])

    venture = by_name["Naming my consulting side project"]
    assert venture["sensitivity"] == "Yellow"
    assert len(venture["sensitivity_reasons"]) == 2


def test_transcript_returns_ordered_messages():
    c = client()
    _, data = upload(c)
    token = data["token"]

    res = c.get(f"/api/transcript?token={token}&index=0")
    body = res.get_json()
    assert res.status_code == 200
    assert body["name"] == "Weekend sourdough troubleshooting"
    assert [m["sender"] for m in body["messages"]] == [
        "human", "assistant", "human", "assistant",
    ]
    assert "sourdough keeps coming out dense" in body["messages"][0]["text"]


def test_transcript_flags_the_text_that_triggered_the_tag():
    c = client()
    _, data = upload(c)
    row = next(r for r in data["conversations"]
               if r["name"] == "Deploy script keeps failing on staging")

    body = c.get(f"/api/transcript?token={data['token']}&index={row['index']}").get_json()
    first = body["messages"][0]

    # Segments must rebuild the message exactly — highlighting can't alter text.
    assert "".join(seg["text"] for seg in first["segments"]) == first["text"]

    flagged = {seg["text"]: seg["tier"] for seg in first["segments"] if seg.get("tier")}
    assert flagged.get("AKIAIOSFODNN7EXAMPLE") == "Red"
    assert flagged.get("192.168.4.31") == "Yellow"   # both tiers are surfaced
    assert all(seg.get("label") for seg in first["segments"] if seg.get("tier"))


def test_both_sides_of_the_transcript_are_highlighted():
    """Highlighting covers the whole transcript, the model's replies included."""
    c = client()
    _, data = upload(c)
    row = next(r for r in data["conversations"]
               if r["name"] == "Naming my consulting side project")

    body = c.get(f"/api/transcript?token={data['token']}&index={row['index']}").get_json()
    human, assistant = body["messages"][0], body["messages"][1]
    assert human["sender"] == "human" and assistant["sender"] == "assistant"

    assert any(seg.get("tier") for seg in human["segments"])
    # The assistant repeats "landing page" — a yellow trigger — and is flagged too.
    assert "landing page" in assistant["text"]
    flagged = [seg["text"] for seg in assistant["segments"] if seg.get("tier")]
    assert "landing page" in flagged
    assert "".join(seg["text"] for seg in assistant["segments"]) == assistant["text"]


def test_tag_reflects_content_the_model_said():
    """Sensitive text in the AI's reply drives the tag, not just user disclosures."""
    c = client()
    _, data = upload(c)
    row = next(r for r in data["conversations"]
               if r["name"] == "Keeping API keys out of git")

    # The user only asked a general question, but Claude's reply prints a
    # live-looking key — so the conversation is Red, driven by the model's output.
    assert row["sensitivity"] == "Red"
    assert any("secrets" in reason for reason in row["sensitivity_reasons"])

    body = c.get(f"/api/transcript?token={data['token']}&index={row['index']}").get_json()
    human, assistant = body["messages"][0], body["messages"][1]

    # The trigger is in the model's message, and nothing in the user's.
    assert not any(seg.get("tier") for seg in human["segments"])
    flagged = {seg["text"]: seg["tier"] for seg in assistant["segments"] if seg.get("tier")}
    assert flagged == {"sk_live_51H8xQ2LmNpQrS7vT9uW": "Red"}


def test_green_transcript_has_no_highlights():
    c = client()
    _, data = upload(c)
    row = next(r for r in data["conversations"]
               if r["name"] == "Weekend sourdough troubleshooting")

    body = c.get(f"/api/transcript?token={data['token']}&index={row['index']}").get_json()
    for message in body["messages"]:
        assert not any(seg.get("tier") for seg in message["segments"])


def test_transcript_falls_back_to_content_blocks():
    c = client()
    _, data = upload(c)
    token = data["token"]
    index = next(
        r["index"] for r in data["conversations"]
        if r["name"] == "Message stored only as content blocks"
    )

    body = c.get(f"/api/transcript?token={token}&index={index}").get_json()
    assert len(body["messages"]) == 2
    assert body["messages"][1]["text"].startswith("Right — so a reader should fall back")


def test_transcript_of_empty_conversation_is_empty():
    c = client()
    _, data = upload(c)
    index = next(
        r["index"] for r in data["conversations"] if r["message_count"] == 0
    )
    body = c.get(f"/api/transcript?token={data['token']}&index={index}").get_json()
    assert body["messages"] == []


def _deploy_row(c, data):
    return next(r for r in data["conversations"]
                if r["name"] == "Deploy script keeps failing on staging")


def test_redaction_boxes_the_flagged_text_everywhere_in_that_message():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)

    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    message = body["messages"][0]
    secret = "AKIAIOSFODNN7EXAMPLE"
    assert secret in message["text"]

    res = c.post("/api/redact", json={
        "token": token,
        "conversation_uuid": row["uuid"],
        "message_uuid": message["uuid"],
        "text": secret,
    })
    assert res.status_code == 200
    assert res.get_json()["redactions"] == 1

    after = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    segments = after["messages"][0]["segments"]
    # The run stays visible so the user can see what they redacted, but now as a
    # `redacted` segment (a greyed box), not plain or flagged text.
    redacted = [s["text"] for s in segments if s.get("redacted")]
    assert secret in redacted
    # The rest of the message survives untouched.
    assert "The deploy dies when it hits the staging box" in after["messages"][0]["text"]
    # And it's no longer a flagged run, so it can't be redacted twice.
    flagged = [s["text"] for s in segments if s.get("tier")]
    assert secret not in flagged
    # It's stripped from the export even though it still shows in the transcript.
    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [row["uuid"]]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    assert secret not in json.dumps(out)


def test_redaction_reaches_the_content_blocks_too():
    """`text` and `content` duplicate each other — a leak in either one counts."""
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = next(r for r in data["conversations"]
               if r["name"] == "Naming my consulting side project")
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()

    c.post("/api/redact", json={
        "token": token,
        "conversation_uuid": row["uuid"],
        "message_uuid": body["messages"][0]["uuid"],
        "text": "side project",
    })

    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [row["uuid"]]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    message = out[0]["chat_messages"][0]

    assert "side project" not in message["text"]
    for block in message["content"]:
        assert "side project" not in block.get("text", "")


def test_redaction_survives_into_the_downloaded_file():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()

    for secret in ("AKIAIOSFODNN7EXAMPLE", "192.168.4.31"):
        res = c.post("/api/redact", json={
            "token": token,
            "conversation_uuid": row["uuid"],
            "message_uuid": body["messages"][0]["uuid"],
            "text": secret,
        })
        assert res.status_code == 200

    # Remove mode keeps everything else — the redaction must ride along.
    c.post("/api/build", json={"token": token, "mode": "remove", "uuids": [
        next(r["uuid"] for r in data["conversations"] if r["name"] == "Trip notes: Lisbon in November")
    ]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    blob = json.dumps(out)
    assert "AKIAIOSFODNN7EXAMPLE" not in blob
    assert "192.168.4.31" not in blob
    # Structure is untouched — only the flagged substrings are gone.
    kept = next(conv for conv in out if conv["uuid"] == row["uuid"])
    assert set(kept) == {"uuid", "name", "created_at", "updated_at", "account", "chat_messages"}
    assert len(kept["chat_messages"]) == 2


def test_redaction_refreshes_the_row_and_can_clear_the_tag():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    assert row["sensitivity"] == "Red"
    before_words = row["word_count"]

    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    message = body["messages"][0]
    flagged = [s["text"] for s in message["segments"] if s.get("tier")]

    latest = None
    for target in flagged:
        res = c.post("/api/redact", json={
            "token": token,
            "conversation_uuid": row["uuid"],
            "message_uuid": message["uuid"],
            "text": target,
        })
        assert res.status_code == 200, res.get_json()
        latest = res.get_json()["row"]

    # With every flagged run gone the conversation drops to Green.
    assert latest["sensitivity"] == "Green"
    assert latest["sensitivity_reasons"] == []
    assert latest["word_count"] < before_words
    assert latest["uuid"] == row["uuid"] and latest["index"] == row["index"]


def test_redaction_rejects_text_that_isnt_flagged():
    """This is a redaction tool, not a general find-and-delete endpoint."""
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    message = body["messages"][0]

    res = c.post("/api/redact", json={
        "token": token,
        "conversation_uuid": row["uuid"],
        "message_uuid": message["uuid"],
        "text": "The deploy dies",
    })
    assert res.status_code == 400

    for bad in [
        {"conversation_uuid": "nope", "message_uuid": message["uuid"], "text": "AKIAIOSFODNN7EXAMPLE"},
        {"conversation_uuid": row["uuid"], "message_uuid": "nope", "text": "AKIAIOSFODNN7EXAMPLE"},
        {"conversation_uuid": row["uuid"], "message_uuid": message["uuid"], "text": ""},
    ]:
        assert c.post("/api/redact", json={"token": token, **bad}).status_code == 400

    # Nothing was written despite the failures.
    after = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    assert "AKIAIOSFODNN7EXAMPLE" in after["messages"][0]["text"]


def test_redaction_invalidates_a_previously_built_file():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)

    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [row["uuid"]]})
    assert c.get(f"/api/download?token={token}").status_code == 200

    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    c.post("/api/redact", json={
        "token": token,
        "conversation_uuid": row["uuid"],
        "message_uuid": body["messages"][0]["uuid"],
        "text": "AKIAIOSFODNN7EXAMPLE",
    })
    # The stale download is withdrawn rather than served with the old text.
    assert c.get(f"/api/download?token={token}").status_code == 400


def test_delete_text_boxes_an_arbitrary_selection():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    msg = body["messages"][0]
    assert "staging box" in msg["text"]

    res = c.post("/api/delete_text", json={
        "token": token, "conversation_uuid": row["uuid"],
        "message_uuid": msg["uuid"], "text": "staging box", "occurrence": 0,
    })
    assert res.status_code == 200
    # A text removal is tallied as a redaction.
    assert res.get_json()["redactions"] == 1

    after = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    # The selection becomes a redacted box, visible in the transcript…
    redacted = [s["text"] for s in after["messages"][0]["segments"] if s.get("redacted")]
    assert "staging box" in redacted
    # …with everything around it untouched…
    assert "The deploy dies when it hits the" in after["messages"][0]["text"]
    # …and stripped from the export.
    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [row["uuid"]]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    assert "staging box" not in json.dumps(out)


def test_delete_text_removes_only_the_selected_occurrence():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = next(r for r in data["conversations"]
               if r["name"] == "Weekend sourdough troubleshooting")
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    # Claude's first reply says "starter" twice; delete only the second one.
    claude = body["messages"][1]
    assert claude["text"].count("starter") >= 2

    res = c.post("/api/delete_text", json={
        "token": token, "conversation_uuid": row["uuid"],
        "message_uuid": claude["uuid"], "text": "starter", "occurrence": 1,
    })
    assert res.status_code == 200
    after = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    segments = after["messages"][1]["segments"]
    # Exactly one "starter" was boxed — not all of them.
    redacted = [s for s in segments if s.get("redacted") and s["text"] == "starter"]
    assert len(redacted) == 1
    # The others are still visible, unredacted.
    visible = "".join(s["text"] for s in segments if not s.get("redacted"))
    assert visible.count("starter") == claude["text"].count("starter") - 1


def test_delete_text_reaches_the_download_and_content_blocks():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    msg = body["messages"][0]

    c.post("/api/delete_text", json={
        "token": token, "conversation_uuid": row["uuid"],
        "message_uuid": msg["uuid"], "text": "AKIAIOSFODNN7EXAMPLE", "occurrence": 0,
    })
    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [row["uuid"]]})
    out = json.loads(c.get(f"/api/download?token={token}").data)

    kept = out[0]
    assert set(kept) == {"uuid", "name", "created_at", "updated_at", "account", "chat_messages"}
    # Gone from both text and the content blocks that mirror it.
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(out)


def test_delete_text_can_be_undone():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    msg = body["messages"][0]
    original = msg["text"]

    c.post("/api/delete_text", json={
        "token": token, "conversation_uuid": row["uuid"],
        "message_uuid": msg["uuid"], "text": "staging box", "occurrence": 0,
    })
    c.post("/api/undo", json={"token": token, "conversation_uuid": row["uuid"]})
    back = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    assert back["messages"][0]["text"] == original


def test_delete_text_rejects_a_selection_that_no_longer_matches():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    msg = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()["messages"][0]

    for bad in [
        {"text": "text that is not present", "occurrence": 0},
        {"text": "staging box", "occurrence": 5},   # no 6th occurrence
        {"text": "", "occurrence": 0},
    ]:
        assert c.post("/api/delete_text", json={
            "token": token, "conversation_uuid": row["uuid"],
            "message_uuid": msg["uuid"], **bad,
        }).status_code == 400


def _delete(c, token, conversation_uuid, message_uuids):
    return c.post("/api/delete_messages", json={
        "token": token,
        "conversation_uuid": conversation_uuid,
        "message_uuids": message_uuids,
    })


def test_mass_delete_removes_selected_messages_and_refreshes_the_row():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    assert row["sensitivity"] == "Red" and row["message_count"] == 2

    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    user_msg = body["messages"][0]  # the one holding the key + IP

    res = _delete(c, token, row["uuid"], [user_msg["uuid"]])
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["deletions"] == 1
    assert payload["redactions"] == 0
    assert payload["can_undo"] is True and payload["can_redo"] is False

    fresh = payload["row"]
    assert fresh["message_count"] == 1
    # The only flagged content lived in the deleted message, so the tag clears.
    assert fresh["sensitivity"] == "Green"

    after = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    assert [m["uuid"] for m in after["messages"]] == [body["messages"][1]["uuid"]]


def test_mass_delete_takes_several_messages_in_one_call():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = next(r for r in data["conversations"]
               if r["name"] == "Weekend sourdough troubleshooting")
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    doomed = [body["messages"][0]["uuid"], body["messages"][2]["uuid"]]

    res = _delete(c, token, row["uuid"], doomed)
    payload = res.get_json()
    assert payload["deletions"] == 2                 # counted per message
    assert payload["row"]["message_count"] == 2      # 4 - 2

    after = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    surviving = [m["uuid"] for m in after["messages"]]
    assert doomed[0] not in surviving and doomed[1] not in surviving


def test_mass_delete_survives_into_the_download_and_keeps_structure():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    doomed = body["messages"][0]["uuid"]

    _delete(c, token, row["uuid"], [doomed])
    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [row["uuid"]]})
    out = json.loads(c.get(f"/api/download?token={token}").data)

    kept = out[0]
    assert kept["uuid"] == row["uuid"]
    # Structure untouched — only the one message is gone.
    assert set(kept) == {"uuid", "name", "created_at", "updated_at", "account", "chat_messages"}
    assert [m["uuid"] for m in kept["chat_messages"]] == [body["messages"][1]["uuid"]]
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(out)


def test_delete_every_message_leaves_an_empty_conversation():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()

    res = _delete(c, token, row["uuid"], [m["uuid"] for m in body["messages"]])
    assert res.get_json()["row"]["message_count"] == 0

    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [row["uuid"]]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    assert out[0]["uuid"] == row["uuid"]        # conversation still present…
    assert out[0]["chat_messages"] == []        # …just emptied


def test_mass_delete_invalidates_a_built_file_and_rejects_bad_targets():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    good = body["messages"][0]["uuid"]

    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [row["uuid"]]})
    assert c.get(f"/api/download?token={token}").status_code == 200

    assert _delete(c, token, "nope", [good]).status_code == 400
    assert _delete(c, token, row["uuid"], ["nope"]).status_code == 400
    assert _delete(c, token, row["uuid"], []).status_code == 400

    # A failed delete leaves the built file intact…
    assert c.get(f"/api/download?token={token}").status_code == 200
    # …but a real one invalidates it.
    _delete(c, token, row["uuid"], [good])
    assert c.get(f"/api/download?token={token}").status_code == 400


def test_undo_and_redo_reverse_and_replay_an_edit():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    idx, uuid = row["index"], row["uuid"]

    body = c.get(f"/api/transcript?token={token}&index={idx}").get_json()
    assert body["can_undo"] is False and body["can_redo"] is False
    original = [m["uuid"] for m in body["messages"]]

    _delete(c, token, uuid, [original[0]])

    undo = c.post("/api/undo", json={"token": token, "conversation_uuid": uuid}).get_json()
    assert undo["deletions"] == 0
    assert undo["row"]["message_count"] == 2
    assert undo["row"]["sensitivity"] == "Red"        # the key is back
    assert undo["can_undo"] is False and undo["can_redo"] is True
    restored = c.get(f"/api/transcript?token={token}&index={idx}").get_json()
    assert [m["uuid"] for m in restored["messages"]] == original

    redo = c.post("/api/redo", json={"token": token, "conversation_uuid": uuid}).get_json()
    assert redo["deletions"] == 1
    assert redo["row"]["message_count"] == 1
    assert redo["can_undo"] is True and redo["can_redo"] is False


def test_undo_reverts_a_redaction():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    body = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    msg = body["messages"][0]

    c.post("/api/redact", json={
        "token": token, "conversation_uuid": row["uuid"],
        "message_uuid": msg["uuid"], "text": "AKIAIOSFODNN7EXAMPLE",
    })
    gone = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    # Redacted: shown as a box, no longer a flagged run.
    gone_segs = gone["messages"][0]["segments"]
    assert "AKIAIOSFODNN7EXAMPLE" in [s["text"] for s in gone_segs if s.get("redacted")]
    assert "AKIAIOSFODNN7EXAMPLE" not in [s["text"] for s in gone_segs if s.get("tier")]

    undo = c.post("/api/undo", json={"token": token, "conversation_uuid": row["uuid"]}).get_json()
    assert undo["redactions"] == 0
    back = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()
    back_segs = back["messages"][0]["segments"]
    # Undo restores it as an ordinary flagged run again — the box is gone.
    assert "AKIAIOSFODNN7EXAMPLE" not in [s["text"] for s in back_segs if s.get("redacted")]
    assert "AKIAIOSFODNN7EXAMPLE" in [s["text"] for s in back_segs if s.get("tier")]


def test_a_new_edit_after_undo_drops_the_redo_branch():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = next(r for r in data["conversations"]
               if r["name"] == "Weekend sourdough troubleshooting")
    uuid = row["uuid"]
    msgs = [m["uuid"] for m in
            c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()["messages"]]

    _delete(c, token, uuid, [msgs[0]])
    c.post("/api/undo", json={"token": token, "conversation_uuid": uuid})
    # A fresh edit from the undone state discards the redo branch.
    branch = _delete(c, token, uuid, [msgs[1]]).get_json()
    assert branch["can_redo"] is False

    survivors = [m["uuid"] for m in
                 c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()["messages"]]
    assert msgs[0] in survivors and msgs[1] not in survivors


def test_undo_or_redo_past_the_ends_is_rejected():
    c = client()
    _, data = upload(c)
    token = data["token"]
    row = _deploy_row(c, data)
    uuid = row["uuid"]

    # Nothing edited yet: both ends are empty.
    assert c.post("/api/undo", json={"token": token, "conversation_uuid": uuid}).status_code == 400
    assert c.post("/api/redo", json={"token": token, "conversation_uuid": uuid}).status_code == 400

    msg = c.get(f"/api/transcript?token={token}&index={row['index']}").get_json()["messages"][0]
    _delete(c, token, uuid, [msg["uuid"]])
    assert c.post("/api/redo", json={"token": token, "conversation_uuid": uuid}).status_code == 400
    c.post("/api/undo", json={"token": token, "conversation_uuid": uuid})
    assert c.post("/api/undo", json={"token": token, "conversation_uuid": uuid}).status_code == 400


def test_export_keeps_only_selected_and_preserves_structure():
    c = client()
    _, data = upload(c)
    token = data["token"]
    picked = [data["conversations"][0]["uuid"], data["conversations"][2]["uuid"]]

    res = c.post("/api/build", json={"token": token, "mode": "export", "uuids": picked})
    assert res.status_code == 200
    assert res.get_json()["conversations_in_file"] == 2

    out = json.loads(c.get(f"/api/download?token={token}").data)
    original = json.load(open(SAMPLE))

    assert [conv["uuid"] for conv in out] == picked
    # each kept conversation is byte-for-byte the original object
    by_uuid = {conv["uuid"]: conv for conv in original}
    for conv in out:
        assert conv == by_uuid[conv["uuid"]]
        assert set(conv) == set(by_uuid[conv["uuid"]])


def test_remove_drops_selected_and_keeps_everything_else():
    c = client()
    _, data = upload(c)
    token = data["token"]
    dropped = [data["conversations"][1]["uuid"]]

    res = c.post("/api/build", json={"token": token, "mode": "remove", "uuids": dropped})
    assert res.get_json()["conversations_in_file"] == 9  # 10 in the file, minus 1

    out = json.loads(c.get(f"/api/download?token={token}").data)
    original = json.load(open(SAMPLE))

    assert dropped[0] not in [conv["uuid"] for conv in out]
    # ordering is preserved, and the untitled conversation survives
    assert [conv["uuid"] for conv in out] == [
        conv["uuid"] for conv in original if conv["uuid"] not in dropped
    ]
    assert any(conv["name"] == "" for conv in out)


def test_download_filename_matches_mode():
    c = client()
    _, data = upload(c)
    token = data["token"]
    uuids = [data["conversations"][0]["uuid"]]

    assert c.post("/api/build", json={"token": token, "mode": "export", "uuids": uuids}) \
        .get_json()["filename"] == "exported_conversations.json"
    assert c.post("/api/build", json={"token": token, "mode": "remove", "uuids": uuids}) \
        .get_json()["filename"] == "conversations_filtered.json"


def test_rejected_inputs():
    c = client()
    _, data = upload(c)
    token = data["token"]

    assert c.post("/api/build", json={"token": token, "mode": "export", "uuids": []}) \
        .status_code == 400
    assert c.post("/api/build", json={"token": token, "mode": "shred", "uuids": ["x"]}) \
        .status_code == 400
    assert c.get("/api/transcript?token=nope&index=0").status_code == 404
    assert c.get(f"/api/download?token={token}").status_code == 400  # nothing built yet


# --------------------------------------------------------------------------
# Soft-deleting whole conversations, with undo / redo
# --------------------------------------------------------------------------

def test_deleted_conversation_is_soft_and_excluded_from_export():
    c = client()
    _, data = upload(c)
    token = data["token"]
    victim = data["conversations"][0]["uuid"]
    keep = data["conversations"][1]["uuid"]

    res = c.post("/api/delete_conversations", json={"token": token, "uuids": [victim]})
    assert res.status_code == 200
    body = res.get_json()
    assert body["deleted"] == [victim]
    assert body["can_undo_delete"] and not body["can_redo_delete"]

    # The row is still parseable (soft delete keeps it in the file)...
    listed = flask_app.claude_parser.list_conversations(
        flask_app.claude_parser.load_raw(flask_app.SESSIONS[token]["source"]))
    assert victim in {conv["uuid"] for conv in listed}

    # ...but it's kept out of the export, even when explicitly selected.
    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [victim, keep]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    ids = [conv["uuid"] for conv in out]
    assert victim not in ids and keep in ids


def test_delete_can_be_undone_and_redone():
    c = client()
    _, data = upload(c)
    token = data["token"]
    victim = data["conversations"][2]["uuid"]

    c.post("/api/delete_conversations", json={"token": token, "uuids": [victim]})

    undo = c.post("/api/undo_delete", json={"token": token}).get_json()
    assert undo["restored"] == [victim]
    assert not undo["can_undo_delete"] and undo["can_redo_delete"]

    # Undone: it exports again.
    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [victim]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    assert victim in [conv["uuid"] for conv in out]

    redo = c.post("/api/redo_delete", json={"token": token}).get_json()
    assert redo["deleted"] == [victim]
    assert redo["can_undo_delete"] and not redo["can_redo_delete"]

    c.post("/api/build", json={"token": token, "mode": "export", "uuids": [victim]})
    out = json.loads(c.get(f"/api/download?token={token}").data)
    assert victim not in [conv["uuid"] for conv in out]


def test_a_fresh_delete_clears_the_redo_stack():
    c = client()
    _, data = upload(c)
    token = data["token"]
    a = data["conversations"][0]["uuid"]
    b = data["conversations"][1]["uuid"]

    c.post("/api/delete_conversations", json={"token": token, "uuids": [a]})
    c.post("/api/undo_delete", json={"token": token})            # redo now available
    body = c.post("/api/delete_conversations", json={"token": token, "uuids": [b]}).get_json()
    assert not body["can_redo_delete"]                           # a new delete drops it
    assert c.post("/api/redo_delete", json={"token": token}).status_code == 400


def test_undo_or_redo_delete_past_the_ends_is_rejected():
    c = client()
    _, data = upload(c)
    token = data["token"]
    assert c.post("/api/undo_delete", json={"token": token}).status_code == 400
    assert c.post("/api/redo_delete", json={"token": token}).status_code == 400

    bad = c.post(
        "/api/upload",
        data={"platform": "claude", "file": (io.BytesIO(b"{not json"), "x.json")},
        content_type="multipart/form-data",
    )
    assert bad.status_code == 400

    wrong_shape = c.post(
        "/api/upload",
        data={"platform": "claude", "file": (io.BytesIO(b'[{"foo": 1}]'), "x.json")},
        content_type="multipart/form-data",
    )
    assert wrong_shape.status_code == 400

    unsupported = c.post(
        "/api/upload",
        data={"platform": "gemini", "file": (io.BytesIO(b"[]"), "x.json")},
        content_type="multipart/form-data",
    )
    assert unsupported.status_code == 400


def test_reset_clears_the_session():
    c = client()
    _, data = upload(c)
    token = data["token"]
    assert c.post("/api/reset", json={"token": token}).status_code == 200
    assert c.get(f"/api/transcript?token={token}&index=0").status_code == 404


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
