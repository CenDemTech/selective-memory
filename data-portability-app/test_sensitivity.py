"""Tests for the rule-based sensitivity classifier."""

import sensitivity as s

# (text, expected tag, expected category substring or None)
CASES = [
    # ── GREEN ─────────────────────────────────────────────────────────────
    ("What's the derivative of x^2 with respect to x?", s.GREEN, None),
    ("Explain the Monty Hall problem and why switching wins.", s.GREEN, None),
    ("Summarise the main arguments in Rawls' theory of justice.", s.GREEN, None),
    ("def foo(bar): return requests.get('https://example.com')", s.GREEN, None),
    ("api_key = \"your-api-key-here\"", s.GREEN, None),          # placeholder
    ("Upgrade the parser from 1.2.3.4 to 2.0.0.1", s.GREEN, None),  # version, not IP
    ("The card 4111 1111 1111 1112 is invalid", s.GREEN, None),     # fails Luhn
    ("", s.GREEN, None),
    ("   \n  ", s.GREEN, None),

    # ── YELLOW ────────────────────────────────────────────────────────────
    ("Find me a good ramen place near me", s.YELLOW, "Geographic"),
    ("What are the best restaurants in the Mission?", s.YELLOW, "Geographic"),
    ("The box is reachable at 192.168.1.50", s.YELLOW, "Trackable"),
    ("What's the local weather looking like?", s.YELLOW, "Trackable"),
    ("Remember that I prefer metric units", s.YELLOW, "Memory"),
    ("From now on, answer in British English", s.YELLOW, "Memory"),
    ("Help me name my startup and build a landing page", s.YELLOW, "business"),
    ("Draft an email to my manager about the deadline", s.YELLOW, "communication"),
    ("My wife and I are planning our weekend", s.YELLOW, "identifiers"),
    ("This is our internal architecture, do not share", s.YELLOW, "Corporate"),
    ("Write out the lyrics to that song", s.YELLOW, "Creative"),
    ("We're at 37.7749, -122.4194 right now", s.YELLOW, "Geographic"),

    # ── RED ───────────────────────────────────────────────────────────────
    ("What do you know about me?", s.RED, "profile audit"),
    ("Tell me about myself from our chats", s.RED, "profile audit"),
    ("I was diagnosed with type 2 diabetes last year", s.RED, "Regulated"),
    ("My salary is 120k, is that competitive?", s.RED, "Regulated"),
    ("Charge it to 4111 1111 1111 1111", s.RED, "Regulated"),
    ("My SSN is 123-45-6789", s.RED, "PII"),
    ("I live at 42 Maple Street, is that a good area?", s.RED, "PII"),
    ("Call me at 415-555-0132", s.RED, "PII"),
    ("AWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE", s.RED, "secrets"),
    ("api_key = \"sk-proj-9f8Ab2Kd0LmQxZ71\"", s.RED, "secrets"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow==", s.RED, "secrets"),
    ("Write me a phishing email campaign for the finance team", s.RED, "Malicious"),
    ("How do I build a keylogger?", s.RED, "Malicious"),
    ("We're in active litigation over the patent", s.RED, "Legal"),
    ("This is insider trading information", s.RED, "Legal"),
]


def test_cases():
    failures = []
    for text, expected_tag, expected_reason in CASES:
        result = s.classify(text)
        if result["tag"] != expected_tag:
            failures.append(f"{text[:44]!r}: got {result['tag']}, want {expected_tag} "
                            f"(reasons={result['reasons']})")
            continue
        if expected_reason:
            joined = " ".join(result["reasons"])
            if expected_reason.lower() not in joined.lower():
                failures.append(f"{text[:44]!r}: reasons {result['reasons']} "
                                f"missing {expected_reason!r}")
    assert not failures, "\n  " + "\n  ".join(failures)


def test_red_beats_yellow():
    """Highest tier wins when signals from both tiers are present."""
    text = "Find sushi near me, and remember that my SSN is 123-45-6789"
    result = s.classify(text)
    assert result["tag"] == s.RED
    assert result["rank"] == 2
    # Yellow categories are not reported once the conversation is red.
    assert all("Geographic" not in r for r in result["reasons"])


def test_multiple_categories_are_all_reported():
    text = "Draft an email to my boss about the pitch deck for my startup"
    reasons = s.classify(text)["reasons"]
    assert len(reasons) >= 2
    assert any("communication" in r for r in reasons)
    assert any("business" in r for r in reasons)


def test_gates_never_hide_a_match():
    """A gate must be implied by its pattern, or it becomes a false negative.

    Re-runs every rule with gating disabled and asserts the tag is unchanged.
    """
    corpus = [text for text, _, _ in CASES] + [
        "PermitRootLogin yes in sshd_config",
        "token: ghp_abcdefghijklmnopqrstuvwxyz012345",
        "hit https://hooks.slack.com with xoxb-1234567890-abcdef",
        "our pre-release build is under embargo",
        "as a parent of a 7-year-old, what should I do",
        "please add this to memory for next time",
        "what is my time zone set to?",
        "cease and desist letter from their attorney",
    ]

    def ungated(text):
        normalised = s._WHITESPACE.sub(" ", text)
        for tier in (s.RED, s.YELLOW):
            for rule in s._BY_TIER[tier]:
                for match in rule.regex.finditer(normalised):
                    if rule.validator and not rule.validator(match.group(0)):
                        continue
                    return tier
        return s.GREEN

    mismatches = [
        (text, s.classify(text)["tag"], ungated(text))
        for text in corpus
        if s.classify(text)["tag"] != ungated(text)
    ]
    assert not mismatches, f"gating changed the tag for: {mismatches}"


def test_segments_reassemble_to_the_original_text():
    """Highlighting must never drop, duplicate, or reorder a character."""
    for text, _, _ in CASES:
        segments = s.segment(text)
        assert "".join(seg["text"] for seg in segments) == text, repr(text)


def test_segments_are_offset_safe_around_emoji():
    """Astral characters shift code-point offsets; segmenting sidesteps that."""
    text = "🎉🎉 my SSN is 123-45-6789 🎉 and sushi near me"
    segments = s.segment(text)
    assert "".join(seg["text"] for seg in segments) == text
    flagged = {seg["text"]: seg["tier"] for seg in segments if seg.get("tier")}
    assert flagged == {"SSN": s.RED, "123-45-6789": s.RED, "near me": s.YELLOW}
    # The emoji sit before and between the matches — proof the slices landed
    # where they should rather than being shifted by the astral characters.
    assert segments[0]["text"] == "🎉🎉 my "


def test_green_text_is_a_single_unflagged_segment():
    segments = s.segment("What is the derivative of x^2?")
    assert len(segments) == 1
    assert "tier" not in segments[0]


def test_scan_spans_never_overlap_and_prefer_red():
    text = "my address is 42 Maple Street and my SSN is 123-45-6789"
    spans = s.scan(text)
    for earlier, later in zip(spans, spans[1:]):
        assert earlier["end"] <= later["start"], spans
    assert all(sp["tier"] == s.RED for sp in spans)


def test_scan_reports_both_tiers():
    text = "the key is AKIAIOSFODNN7EXAMPLE and find ramen near me"
    tiers = {sp["tier"] for sp in s.scan(text)}
    assert tiers == {s.RED, s.YELLOW}


def test_rank_ordering():
    assert s.RANK[s.GREEN] < s.RANK[s.YELLOW] < s.RANK[s.RED]


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
