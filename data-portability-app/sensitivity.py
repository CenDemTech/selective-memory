"""Rule-based sensitivity tagging for conversation text.

No model calls: this is lexical pattern matching — normalised text, word-boundary
regexes, and a handful of structural validators (Luhn for card numbers, octet
ranges for IP addresses, placeholder detection for credential assignments).

Each conversation gets one of three tags:

    RED     any high-risk signal — regulated data, PII, live secrets, exploits,
            legal exposure, or the user asking what the model knows about them
    YELLOW  quasi-identifying or commercially sensitive signals — location,
            trackable context, memory writes, business plans, personal routines
    GREEN   nothing matched

Highest tier wins, so a conversation with one red hit and nine yellow ones is
red. ``classify`` also returns *which* categories fired so the UI can explain
the tag rather than just asserting it.

Each rule carries a list of ``gates``: cheap lowercase literals, at least one of
which must appear for the rule's regex to have any chance of matching. A gate
must be a *necessary* condition for the pattern, never merely a likely one —
a wrong gate silently turns into a false negative. Substring scanning is roughly
two orders of magnitude faster than the alternations it guards, so on ordinary
conversations most rules cost nothing.
"""

import re

GREEN, YELLOW, RED = "Green", "Yellow", "Red"
RANK = {GREEN: 0, YELLOW: 1, RED: 2}


class Rule:
    __slots__ = ("key", "tier", "label", "regex", "gates", "validator")

    def __init__(self, key, tier, label, pattern, gates=(), validator=None,
                 ignore_case=True):
        self.key = key
        self.tier = tier
        self.label = label
        self.regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        self.gates = gates
        self.validator = validator


# --------------------------------------------------------------------------
# Structural validators — cut the false positives that pure regex can't
# --------------------------------------------------------------------------

def _luhn(match):
    """Reject digit runs that aren't valid payment card numbers."""
    digits = [int(c) for c in match if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _real_ip(match):
    """Reject version strings like 1.2.3.4 that look like dotted quads."""
    octets = [int(part) for part in match.split(".")]
    if any(o > 255 for o in octets):
        return False
    if octets[0] in (10, 127):
        return True
    if octets[0] == 192 and octets[1] == 168:
        return True
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return True
    # Otherwise insist it doesn't look like a semantic version number.
    return max(octets) >= 32


_PLACEHOLDER = re.compile(
    r"^(?:x{3,}|\*{3,}|\.{3,}|-{3,}|<|\{|\[|\$|%|your|my|the|some|abc|123|"
    r"changeme|change_me|placeholder|example|sample|dummy|fake|test|todo|"
    r"redacted|hidden|omitted|none|null|nil|insert|paste|enter|add)",
    re.IGNORECASE,
)


def _real_secret(match):
    """Reject `api_key = "your-key-here"` style documentation placeholders."""
    parts = re.split(r"[:=]", match, maxsplit=1)
    if len(parts) != 2:
        return False
    value = parts[1].strip().strip("\"'` ")
    if len(value) < 8 or _PLACEHOLDER.match(value):
        return False
    # A real secret has some entropy; a prose sentence does not.
    return not re.search(r"\s", value)


# --------------------------------------------------------------------------
# Category labels
# --------------------------------------------------------------------------

_AUDIT = "User profile audit"
_HEALTH_FIN = "Regulated data (health or finance)"
_PII = "High-risk PII"
_SECRETS = "Live infrastructure secrets"
_MALICIOUS = "Malicious intent or exploits"
_LEGAL = "Legal or high-liability content"

_GEO = "Geographic or proximity query"
_TRACK = "Trackable indicators"
_MEMORY = "Memory retention request"
_BUSINESS = "Potential business venture"
_COMMS = "Interpersonal or professional communication"
_IDENTIFIERS = "Vague or indirect identifiers"
_CORPORATE = "Corporate assets or IP"
_CREATIVE = "Creative or copyrighted material"


RULES = [
    # ── RED ───────────────────────────────────────────────────────────────
    Rule("red_audit", RED, _AUDIT,
         r"(?:what|how\s+much)\s+(?:do\s+)?you\s+(?:know|remember|have)\s+about\s+me"
         r"|tell\s+me\s+about\s+myself"
         r"|describe\s+me\s+based\s+on"
         r"|based\s+on\s+(?:our|all\s+(?:our|my)|my)\s+(?:chats?|conversations?|history)"
         r"|what\s+have\s+you\s+(?:learned|stored|saved|inferred)\s+about\s+me"
         r"|everything\s+you\s+know\s+about\s+me"
         r"|do\s+you\s+(?:remember|track|store|keep)\s+(?:anything\s+about\s+)?me\b"
         r"|(?:my|your)\s+(?:stored\s+)?user\s+profile"
         r"|profile\s+data\s+you",
         gates=("about me", "myself", "based on", "do you ", "user profile", "profile data")),

    Rule("red_health", RED, _HEALTH_FIN,
         r"diagnos(?:ed|is|es)\s+with|my\s+diagnosis|prescri(?:bed|ption)\b"
         r"|medical\s+(?:record|history)|health\s+record|patient\s+(?:record|chart|data)"
         r"|lab\s+results?|blood\s+(?:test|work|pressure)\s+(?:result|reading|of|was)"
         r"|\bmri\b|\bct\s+scan\b|biopsy|chemotherapy|oncologist|psychiatrist"
         r"|antidepressant|\bhipaa\b|\bphi\b",
         gates=("diagnos", "prescri", "medical ", "health record", "patient ", "lab result",
                "blood ", "mri", "ct scan", "biopsy", "chemotherapy", "oncologist",
                "psychiatrist", "antidepressant", "hipaa", "phi")),

    Rule("red_finance", RED, _HEALTH_FIN,
         r"\b(?:routing\s+number|bank\s+account\s+number|credit\s+card\s+number"
         r"|card\s+number|cvv|cvc|iban|swift\s+code|pci[-\s]?dss"
         r"|my\s+salary|salary\s+(?:is|was|package|of)|annual\s+compensation"
         r"|base\s+pay|my\s+net\s+worth|tax\s+return)\b",
         gates=("routing number", "bank account", "credit card", "card number", "cvv",
                "cvc", "iban", "swift code", "pci", "salary", "compensation", "base pay",
                "net worth", "tax return")),

    Rule("red_card", RED, _HEALTH_FIN,
         r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b",
         validator=_luhn),

    Rule("red_ssn", RED, _PII,
         r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),

    Rule("red_pii_terms", RED, _PII,
         r"\b(?:social\s+security\s+(?:number|#)|ssn"
         r"|passport\s+(?:number|no\.?|#)|driver'?s?\s+licen[cs]e\s+(?:number|#)"
         r"|my\s+(?:home\s+)?address\s+is|track\s+my\s+location|live\s+location"
         r"|my\s+(?:current\s+)?(?:gps|coordinates))",
         gates=("social security", "ssn", "passport", "licen", "address is",
                "track my location", "live location", "gps", "coordinates")),

    # Deliberately ungated: a street address is anchored on digits, and every
    # literal suffix ("st", "ave") is too short or too common to gate safely.
    Rule("red_street", RED, _PII,
         r"\b\d{1,6}\s+(?:[a-z0-9'.-]+\s+){1,4}"
         r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr"
         r"|court|ct|place|pl|terrace|circle|cir|highway|hwy)\b"),

    Rule("red_phone", RED, _PII,
         r"\b(?:\+?1[-. ])?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),

    Rule("red_privkey", RED, _SECRETS,
         r"-----BEGIN\s+(?:[A-Z]+\s+)*PRIVATE\s+KEY-----",
         gates=("private key",)),

    Rule("red_token", RED, _SECRETS,
         r"\bAKIA[0-9A-Z]{16}\b"
         r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
         r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
         r"|\bxox[baprs]-[A-Za-z0-9-]{10,}\b"
         r"|\bsk_live_[A-Za-z0-9]{16,}\b"
         r"|\bAIza[0-9A-Za-z_-]{35}\b"
         r"|\bsk-ant-[A-Za-z0-9_-]{20,}\b",
         gates=("akia", "eyj", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "xox",
                "sk_live_", "aiza", "sk-ant-")),

    Rule("red_secret_assign", RED, _SECRETS,
         r"(?:api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?token"
         r"|auth[_-]?token|private[_-]?key|passwd|password)\s*[:=]\s*[\"'`]?[^\s\"'`,;)]{8,}",
         gates=("key", "secret", "token", "passw"),
         validator=_real_secret),

    Rule("red_ssh", RED, _SECRETS, r"PermitRootLogin\s+yes",
         gates=("permitrootlogin",)),

    Rule("red_malicious", RED, _MALICIOUS,
         r"\b(?:malware|ransomware|keylogger|rootkit|botnet|trojan|spyware"
         r"|reverse\s+shell|privilege\s+escalation|credential\s+(?:harvest|stuff)"
         r"|phishing\s+(?:email|campaign|page|site|kit)|sql\s+injection"
         r"|cross[-\s]site\s+scripting|xss\s+payload|zero[-\s]day|exploit\s+(?:kit|chain)"
         r"|bypass\s+(?:the\s+)?(?:security|authentication|firewall|antivirus|alarm|lock)"
         r"|lock\s?picking|ddos|denial[-\s]of[-\s]service)\b",
         gates=("malware", "ransomware", "keylogger", "rootkit", "botnet", "trojan",
                "spyware", "reverse shell", "privilege escalation", "credential ",
                "phishing", "sql injection", "site scripting", "xss", "zero day",
                "zero-day", "exploit ", "bypass ", "lock pick", "lockpick", "ddos",
                "denial of service", "denial-of-service")),

    Rule("red_legal", RED, _LEGAL,
         r"\b(?:active\s+litigation|pending\s+lawsuit|insider\s+trading"
         r"|whistleblow(?:er|ing)?|subpoena|deposition|attorney[-\s]client"
         r"|under\s+(?:an\s+)?nda|non[-\s]disclosure\s+agreement"
         r"|settlement\s+agreement|cease\s+and\s+desist|class\s+action"
         r"|criminal\s+charges)\b",
         gates=("litigation", "lawsuit", "insider trading", "whistleblow", "subpoena",
                "deposition", "attorney", "nda", "disclosure agreement",
                "settlement agreement", "cease and desist", "class action",
                "criminal charges")),

    # ── YELLOW ────────────────────────────────────────────────────────────
    Rule("yel_geo", YELLOW, _GEO,
         r"\b(?:near\s+me|close\s+to\s+me|around\s+me|in\s+my\s+area|nearby"
         r"|around\s+here|in\s+my\s+neighborhood|walking\s+distance"
         r"|things\s+to\s+do\s+in|restaurants?\s+(?:in|near)|zip\s+code"
         r"|postal\s+code|my\s+(?:city|town|neighborhood)|local\s+(?:spots?|places?))\b",
         gates=("near me", "close to me", "around me", "in my area", "nearby",
                "around here", "neighborhood", "walking distance", "things to do in",
                "restaurant", "zip code", "postal code", "my city", "my town",
                "local spot", "local place")),

    Rule("yel_coords", YELLOW, _GEO,
         r"\b-?\d{1,2}\.\d{3,},\s*-?\d{1,3}\.\d{3,}\b"),

    # Case-sensitive on purpose: the capital letter is what distinguishes a
    # named place ("days in Lisbon") from an ordinary phrase ("days in bed").
    Rule("yel_travel", YELLOW, _GEO,
         r"(?:things?\s+to\s+do\s+in|what\s+to\s+do\s+in|itinerary\s+for"
         r"|(?:\d+\s+)?days?\s+in|trip\s+to|travell?(?:ing)?\s+to|visiting"
         r"|flying\s+to|staying\s+in)\s+(?:the\s+)?"
         # Months and weekdays are capitalised too, and "days in December" is a
         # date, not a destination.
         r"(?!Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec"
         r"|Mon|Tues?|Wed|Thur?s?|Fri|Sat|Sun)"
         r"[A-Z][a-z]{2,}",
         gates=("things to do in", "thing to do in", "what to do in", "itinerary for",
                "days in", "day in", "trip to", "travel to", "travelling to",
                "traveling to", "visiting", "flying to", "staying in"),
         ignore_case=False),

    Rule("yel_track", YELLOW, _TRACK,
         r"\b(?:what\s+time\s+is\s+it|current\s+(?:local\s+)?time|my\s+time\s?zone"
         r"|local\s+weather|weather\s+(?:here|today|tomorrow|outside)"
         r"|my\s+(?:ip|browser|device|laptop|phone|screen\s+resolution)"
         r"|user\s+agent|ip\s+address|operating\s+system\s+version)\b",
         gates=("what time is it", "current time", "current local time", "time zone",
                "timezone", "local weather", "weather ", "my ip", "my browser",
                "my device", "my laptop", "my phone", "screen resolution",
                "user agent", "ip address", "operating system")),

    Rule("yel_ip", YELLOW, _TRACK,
         r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
         validator=_real_ip),

    Rule("yel_memory", YELLOW, _MEMORY,
         r"\b(?:remember\s+(?:that|this|my|i|for\s+next\s+time)"
         r"|add\s+(?:this|that|it)\s+to\s+(?:your\s+)?memory"
         r"|save\s+(?:this|that|it)\s+(?:to|in)\s+(?:your\s+)?memory"
         r"|keep\s+(?:this\s+)?in\s+mind|for\s+future\s+reference|from\s+now\s+on"
         r"|update\s+my\s+(?:profile|preferences)|note\s+(?:that|this)\s+for\s+later"
         r"|don'?t\s+forget)\b",
         gates=("remember ", "to memory", "in memory", "keep in mind", "keep this in mind",
                "future reference", "from now on", "update my", "for later",
                "don't forget", "dont forget")),

    Rule("yel_business", YELLOW, _BUSINESS,
         # "at startup" / "on server startup" is program lifecycle, not a venture.
         r"\b(?:(?<!\bat )(?<!\bon )(?<!\bduring )(?<!\bapp )(?<!\bserver )"
         r"(?<!\bsystem )(?<!\bcold )start[-\s]?up"
         r"|business\s+(?:plan|idea|model|strateg\w+)"
         r"|my\s+(?:company|business|startup)|side\s+project|landing\s+page|saas"
         r"|monetiz\w+|pricing\s+(?:model|page|tier)|mvp|go[-\s]to[-\s]market"
         r"|product\s+launch|logo\s+(?:design|idea)|brand\s+name|company\s+name"
         r"|app\s+idea|build\s+(?:a|an|my)\s+(?:website|app|application)"
         r"|revenue\s+model|pitch\s+deck|investor\s+(?:deck|pitch))\b",
         gates=("startup", "start-up", "start up", "business ", "side project",
                "landing page", "saas", "monetiz", "pricing ", "mvp", "go-to-market",
                "go to market", "product launch", "logo ", "brand name", "company name",
                "app idea", "website", "revenue model", "pitch deck", "investor ")),

    Rule("yel_comms", YELLOW, _COMMS,
         r"\b(?:write\s+(?:an?\s+)?(?:email|letter|message|text|note)\s+to"
         r"|draft\s+(?:an?\s+)?(?:email|letter|message|reply|response)"
         r"|reply\s+to\s+(?:this|my|the)\s+(?:email|message|text)"
         r"|(?:email|message)\s+to\s+my|cover\s+letter|letter\s+of\s+recommendation"
         r"|resignation\s+letter|performance\s+review|price\s+quote"
         # An invoice only counts when one is being produced or sent — plenty of
         # ordinary engineering talk mentions invoices as a domain noun.
         r"|(?:generate|create|write|draft|send|prepare|make|issue)\s+(?:an?\s+)?invoice"
         r"|invoice\s+(?:for|to)\s+(?:a|the|my)\s+(?:client|customer)"
         r"|follow[-\s]up\s+email|thank[-\s]you\s+note)\b",
         gates=("email", "letter", "message", "text", "note", "reply to", "invoice",
                "price quote", "performance review")),

    Rule("yel_identifiers", YELLOW, _IDENTIFIERS,
         r"\b(?:i'?m\s+\d{1,2}\s+years?\s+old|i\s+am\s+\d{1,2}\s+years?\s+old"
         r"|\d{1,2}[-\s]year[-\s]old|as\s+a\s+(?:woman|man|father|mother|parent"
         r"|student|teenager|immigrant|retiree)"
         r"|my\s+(?:wife|husband|partner|girlfriend|boyfriend|kids?|children|son"
         r"|daughter|mom|dad|parents|family|roommate)"
         r"|my\s+(?:birthday|age|gender|ethnicity|nationality)"
         r"|my\s+(?:weekend|daily\s+routine|morning\s+routine|schedule|hobby|hobbies)"
         r"|every\s+(?:morning|evening|weekend)\s+i)\b",
         gates=("years old", "year old", "year-old", "as a ", "my wife", "my husband",
                "my partner", "my girlfriend", "my boyfriend", "my kid", "my child",
                "my son", "my daughter", "my mom", "my dad", "my parent", "my family",
                "my roommate", "my birthday", "my age", "my gender", "my ethnicity",
                "my nationality", "my weekend", "my daily routine", "my morning routine",
                "my schedule", "my hobb", "every morning i", "every evening i",
                "every weekend i")),

    Rule("yel_corporate", YELLOW, _CORPORATE,
         r"\b(?:proprietary|confidential|internal\s+(?:only|use\s+only|doc\w*"
         r"|design|architecture|tool\w*)|pre[-\s]release|unreleased|trade\s+secret"
         r"|under\s+embargo|our\s+(?:internal\s+)?codebase|not\s+public\s+yet"
         r"|do\s+not\s+share|company\s+confidential)\b",
         gates=("proprietary", "confidential", "internal ", "pre-release", "pre release",
                "unreleased", "trade secret", "embargo", "codebase", "not public yet",
                "do not share")),

    Rule("yel_creative", YELLOW, _CREATIVE,
         r"\b(?:song\s+lyrics|lyrics|screenplay|movie\s+script|film\s+script"
         r"|copyrighted|chapter\s+\d+\s+of|excerpt\s+from\s+the\s+book"
         r"|full\s+text\s+of\s+the\s+(?:book|article|poem|song))\b",
         gates=("lyrics", "screenplay", "movie script", "film script", "copyrighted",
                "chapter ", "excerpt from", "full text of")),
]

BY_KEY = {rule.key: rule for rule in RULES}
_BY_TIER = {tier: [r for r in RULES if r.tier == tier] for tier in (RED, YELLOW)}

_WHITESPACE = re.compile(r"\s+")


def _gate_text(text):
    """Lowercased, whitespace-collapsed copy used only for gate checks.

    Gates are literal substrings with single spaces, so they need the collapsed
    form to survive a phrase broken across a line. The rules themselves match
    against the original text — every pattern spaces its words with ``\\s+`` —
    which keeps match offsets usable for highlighting.
    """
    return _WHITESPACE.sub(" ", text).lower()


def classify(text):
    """Tag one blob of conversation text.

    Returns ``{"tag": "Red"|"Yellow"|"Green", "rank": int, "reasons": [str]}``.
    """
    if not text or not text.strip():
        return {"tag": GREEN, "rank": RANK[GREEN], "reasons": []}

    lowered = _gate_text(text)

    for tier in (RED, YELLOW):
        reasons = []
        for rule in _BY_TIER[tier]:
            if rule.label in reasons:
                continue  # this category already fired via another rule
            if rule.gates and not any(gate in lowered for gate in rule.gates):
                continue
            for match in rule.regex.finditer(text):
                if rule.validator and not rule.validator(match.group(0)):
                    continue
                reasons.append(rule.label)
                break
        if reasons:
            return {"tag": tier, "rank": RANK[tier], "reasons": reasons}

    return {"tag": GREEN, "rank": RANK[GREEN], "reasons": []}


def scan(text):
    """Every rule match in ``text``, as non-overlapping spans.

    Unlike ``classify`` this doesn't stop at the first hit per rule, and it
    reports both tiers — a red conversation can still contain yellow signals
    worth showing. Where spans overlap, the higher tier wins and the longer
    match wins within a tier.
    """
    if not text or not text.strip():
        return []

    lowered = _gate_text(text)
    found = []
    for tier in (RED, YELLOW):
        for rule in _BY_TIER[tier]:
            if rule.gates and not any(gate in lowered for gate in rule.gates):
                continue
            for match in rule.regex.finditer(text):
                if rule.validator and not rule.validator(match.group(0)):
                    continue
                found.append({
                    "start": match.start(),
                    "end": match.end(),
                    "tier": tier,
                    "label": rule.label,
                })

    # Red before yellow, then longest first, so the strongest signal claims
    # the overlap; anything colliding with an accepted span is dropped.
    found.sort(key=lambda s: (-RANK[s["tier"]], s["start"] - s["end"], s["start"]))
    kept = []
    for span in found:
        if any(span["start"] < k["end"] and k["start"] < span["end"] for k in kept):
            continue
        kept.append(span)

    kept.sort(key=lambda s: s["start"])
    return kept


def segment(text):
    """Split ``text`` into plain and flagged runs, ready for highlighting.

    Returns segments rather than offsets on purpose: Python indexes strings by
    code point and JavaScript by UTF-16 code unit, so any astral character (an
    emoji, say) would shift every offset after it and misplace the highlight.
    Slicing here means the client only has to concatenate.
    """
    if not text:
        return []

    spans = scan(text)
    if not spans:
        return [{"text": text}]

    segments = []
    cursor = 0
    for span in spans:
        if span["start"] > cursor:
            segments.append({"text": text[cursor:span["start"]]})
        segments.append({
            "text": text[span["start"]:span["end"]],
            "tier": span["tier"],
            "label": span["label"],
        })
        cursor = span["end"]
    if cursor < len(text):
        segments.append({"text": text[cursor:]})
    return segments
