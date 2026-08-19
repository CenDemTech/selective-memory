"""Generates sample_data/openai_conversations.json — a small, valid ChatGPT
export exercising system/tool filtering, a tool-authored answer node, a branch,
sensitive content, and an untitled conversation."""

import json
import os

T0 = 1770838263.0


def node(node_id, parent, children, *, role=None, text=None, content_type="text",
         create_time=None, hidden=False, real_author=None):
    message = None
    if role is not None:
        content = ({"content_type": "text", "parts": [text if text is not None else ""]}
                   if content_type == "text"
                   else {"content_type": content_type, "text": text or ""})
        metadata = {}
        if hidden:
            metadata["is_visually_hidden_from_conversation"] = True
        if real_author:
            metadata["real_author"] = real_author
        message = {
            "id": node_id,
            "author": {"role": role, "name": None, "metadata": {}},
            "create_time": create_time,
            "content": content,
            "status": "finished_successfully",
            "metadata": metadata,
            "recipient": "all",
        }
    return node_id, {"id": node_id, "message": message, "parent": parent, "children": children}


def conversation(conv_id, title, create_time, mapping_nodes, current_node):
    mapping = dict(mapping_nodes)
    return {
        "title": title,
        "create_time": create_time,
        "update_time": create_time + 60,
        "mapping": mapping,
        "moderation_results": [],
        "current_node": current_node,
        "conversation_id": conv_id,
        "is_archived": False,
        "is_starred": None,
        "safe_urls": [],
        "default_model_slug": "gpt-4o",
        "id": conv_id,
    }


def simple(conv_id, title, t, user_text, assistant_text, *, tool_answer=False):
    """root(system, hidden) -> user -> [assistant empty] -> answer."""
    nodes = []
    nodes.append(node("root", None, ["sys"]))
    nodes.append(node("sys", "root", ["u"], role="system", text="", hidden=True))
    nodes.append(node("u", "sys", ["a0"], role="user", text=user_text, create_time=t + 1))
    if tool_answer:
        # mimic the real export: an empty assistant node, a code tool call, then a
        # tool-authored answer node that actually holds the visible reply.
        nodes.append(node("a0", "u", ["call"], role="assistant", text="", create_time=t + 2))
        nodes.append(node("call", "a0", ["answer"], role="assistant",
                          text='search("x")', content_type="code", create_time=t + 3))
        nodes.append(node("answer", "call", [], role="assistant", text=assistant_text,
                          create_time=t + 4, real_author="tool:web"))
        current = "answer"
    else:
        nodes.append(node("a0", "u", [], role="assistant", text=assistant_text, create_time=t + 4))
        current = "a0"
    return conversation(conv_id, title, t, nodes, current)


def branched(conv_id, title, t, user_text, old_answer, new_answer):
    """A user turn with two assistant answers; current_node points at the new one."""
    nodes = [
        node("root", None, ["u"]),
        node("u", "root", ["a_old", "a_new"], role="user", text=user_text, create_time=t + 1),
        node("a_old", "u", [], role="assistant", text=old_answer, create_time=t + 2),
        node("a_new", "u", [], role="assistant", text=new_answer, create_time=t + 3),
    ]
    return conversation(conv_id, title, t, nodes, "a_new")


data = [
    simple("c-plans", "Weekend hiking plans", T0,
           "Any tips for a first overnight backpacking trip?",
           "Start with a well-marked trail under 10 miles, pack layers, and test "
           "your gear at home first. Water and a map matter most.",
           tool_answer=True),
    simple("c-key", "Leaked AWS key in a repo", T0 + 100,
           "I accidentally committed AKIAIOSFODNN7EXAMPLE to a public repo. What now?",
           "Rotate that key immediately in the AWS console, then purge it from git "
           "history. Assume it's compromised."),
    simple("c-near", "Dinner spots", T0 + 200,
           "Find me a good sushi place near me for tonight.",
           "I can't see your location, but searching 'sushi near me' in maps will "
           "rank the closest well-reviewed spots."),
    branched("c-branch", "Naming a startup", T0 + 300,
             "Suggest a name for my analytics startup.",
             "How about DataPeak?",
             "Try Quantly, Metricly, or Insightful — short and brandable."),
    simple("c-untitled", "", T0 + 400, "hi", "Hello!"),  # dropped by the title filter
]

out = os.path.join(os.path.dirname(__file__), "openai_conversations.json")
with open(out, "w") as f:
    json.dump(data, f, indent=2)
print("wrote", out, "with", len(data), "conversations")
