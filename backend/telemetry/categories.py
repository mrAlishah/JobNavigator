"""Error texts reduced to a category: the text itself (names, URLs, ids) is never sent."""
import re

_SCRAPE = (
    ("board_not_found", r"BoardNotFound|board .* not found|no such board"),
    ("bot_wall", r"captcha|cloudflare|bot check|access denied|are you a robot|verify you are human"),
    ("http_429", r"\b429\b|too many requests|rate.?limit"),
    ("http_403", r"\b403\b|forbidden"),
    ("http_404", r"\b404\b|\b410\b|not found"),
    ("http_5xx", r"\b5\d\d\b|server disconnected|bad gateway|service unavailable"),
    ("timeout", r"timeout|timed out|TimeoutError|ReadTimeout|ConnectTimeout"),
    ("parse", r"JSONDecode|json decode|KeyError|TypeError|IndexError|AttributeError|parse|NUL \(0x00\)"),
)

_LLM = (
    ("unknown_model", r"unrecognized_model|model[_ ]not[_ ]found|unknown model|does not exist"),
    ("cli_missing", r"rc=127|command not found|No such file or directory|not installed|ENOENT"),
    ("auth", r"\b401\b|invalid.{0,10}api.?key|authentication|unauthori[sz]ed|not logged in|oauth"),
    ("quota", r"quota|credit|billing|insufficient|\b402\b"),
    ("rate_limit", r"\b429\b|rate.?limit|overloaded|\b529\b"),
    ("timeout", r"timeout|timed out|deadline"),
    ("bad_response", r"JSON decode|not valid JSON|Expecting value|Expecting ',' delimiter|malformed|empty response"),
    # a CLI provider (Claude Code, Codex, Antigravity) that exited with an error and said nothing more
    ("cli_error", r"subprocess failed|exited with|\brc=\d+"),
    ("cancelled", r"^cancelled$"),
)


def _match(table, text) -> str:
    for name, pattern in table:
        if re.search(pattern, text, re.I):
            return name
    return "other"


def scrape_error_category(text):
    """None for no error, else a category name."""
    if not text or not str(text).strip():
        return None
    return _match(_SCRAPE, str(text))


def llm_error_category(text) -> str:
    return _match(_LLM, str(text or ""))


def tally(categories) -> str:
    """'timeout=2, http_404=1' (most first), or '' when there are none."""
    counts = {}
    for c in categories:
        if c:
            counts[c] = counts.get(c, 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
