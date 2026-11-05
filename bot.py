#!/usr/bin/env python3
"""Apple Singapore refurbished-stock bot for Telegram.

Runs every 5 minutes on GitHub Actions (started by cron-job.org). Each run it:
  1. reads your Telegram commands and answers them,
  2. downloads the refurbished pages of the sections you watch,
  3. compares them with the last run (state.json) and sends alerts,
  4. sends the daily summary once a day.

User guide: README.md. Setup: SETUP.md. Python 3.8+, standard library only.

PUBLIC REPO: never print Telegram messages, command text or the chat ID. The log only
shows counts.
"""
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.environ.get("STATE_FILE") or os.path.join(HERE, "state.json")
SGT = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
APPLE = "https://www.apple.com"
PAGE = APPLE + "/sg/shop/refurbished/{}"
MARK = "REFURB_GRID_BOOTSTRAP"

CATS = ("mac", "ipad", "watch", "airpods", "appletv", "homepod", "accessories")
CAT_NAMES = {"mac": "Mac", "ipad": "iPad", "watch": "Apple Watch", "airpods": "AirPods",
             "appletv": "Apple TV", "homepod": "HomePod", "accessories": "Accessories"}
# words that name a section. The first group is only a section name; the second also
# narrows the watch (e.g. "imac" = section mac AND the word imac in the product name).
CAT_ALIASES = {"macs": "mac", "ipads": "ipad", "applewatch": "watch", "watches": "watch",
               "airpod": "airpods", "tv": "appletv", "apple-tv": "appletv", "homepods": "homepod",
               "accessory": "accessories"}
CAT_WORDS = {"macbook": "mac", "macbooks": "mac", "imac": "mac", "imacs": "mac"}
# words matched against the product name only ("pro" = MacBook Pro / iPad Pro, not "M4 Pro chip")
PRODUCT_WORDS = {"air", "pro", "mini", "neo", "studio", "imac", "macbook", "ipad", "series", "se",
                 "ultra", "homepod", "pencil", "display", "tv"}
SYNONYMS = (("grey", "gray"), ("aluminium", "aluminum"), ("cellular", "cell"), ("colour", "color"))

DEFAULT_SETTINGS = {"restock": True, "price": "on", "soldout": True, "daily": 9, "health": True,
                    "newmodel": True, "quiet": None, "min_gone": 0}
HEALTH_AFTER_MIN = 30      # Apple failing this long -> bot-health alert
MAX_WATCHES = 50
STOCK_PAGE = 30            # items per /stock reply
GONE_KEEP_DAYS = 60        # remember sold-out items this long (for "back after 3 d")
MENU_VERSION = 1

HELP = """<b>Commands</b> (I read them every 5 minutes, so replies take up to ~5 min)

<b>See what's in stock</b>
/stock: sections and item counts
/stock mac: every Mac, cheapest first, numbered
/stock mac air m4 16gb: only matching items (same words as /watch)
/stock mac air page 2: the next page

<b>Watch</b>
/watch mac: everything in a section
/watch mac air 13 m4 16gb 512gb midnight max=1800: items with those specs
/watch FX2E3ZP/A (or FX2E3): one exact part number
/watch 3: item 3 from your last /stock list (or /watch 3 5 8)
/list: your watches with what's in stock now
/unwatch 2: stop watch 2 (/unwatch all removes all)

<b>Settings</b>
/settings: show and change alerts, quiet hours, daily summary
/status: what the last check saw
/help: this message

The README on GitHub has the full guide."""

WATCH_HELP = """What should I watch? Examples:
<code>/watch mac</code>: every refurbished Mac
<code>/watch mac air m4 16gb</code>: MacBook Air with M4 and 16GB (RAM or SSD)
<code>/watch mac pro 14 m4 pro ram=24gb ssd=1tb max=3000</code>
<code>/watch ipad pro 11 m4 cellular</code>
<code>/watch watch series 11 46mm titanium</code>
<code>/watch mac studio display nano=yes</code>
<code>/watch FX2E3ZP/A</code>: one exact part number
<code>/watch 3</code>: item 3 from your last /stock list

Words: section (mac, ipad, watch, airpods, appletv, homepod, accessories), model words
(air, pro, mini, neo, imac, studio, se, series, ultra...), screen size (13, 14, 15.3, 24) or
case size (42mm), chip (m4, m4 pro, m5 max, a18 pro), cores (10-core, 14-core cpu,
32-core gpu), memory (16gb = RAM or SSD; ram=24gb, ssd=1tb), colour, year (2025),
nano=yes/no/any, max=PRICE. Any other word must appear in Apple's title."""


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------

def log(*a):
    print(*a, flush=True)


def esc(s):
    return html.escape(str(s), quote=False)


def now_utc():
    v = os.environ.get("NOW")  # tests only
    if v:
        d = datetime.fromisoformat(v)
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(d):
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def money(p):
    return "S${:,.0f}".format(p) if float(p).is_integer() else "S${:,.2f}".format(p)


def fmt_dur(seconds):
    m = int(seconds // 60)
    if m < 60:
        return "{} min".format(max(m, 1))
    h, m = divmod(m, 60)
    if h < 48:
        return "{} h {} min".format(h, m) if m else "{} h".format(h)
    d, h = divmod(h, 24)
    return "{} d {} h".format(d, h) if h else "{} d".format(d)


def plural(n, one, many=None):
    return "{} {}".format(n, one if n == 1 else many or one + "s")


def words(text):
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())


def clean_title(t):
    t = (t or "").replace("‑", "-").replace("‐", "-").replace("–", "-")
    t = t.replace("—", "-").replace(" ", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"^Refurbished\s+", "", t)


CHIP_IN_TITLE = re.compile(r"\b([MA]\d{1,2})(?:\s+(Pro|Max|Ultra|Bionic))?\b")


def chip_of(title):
    m = CHIP_IN_TITLE.search(title)
    if not m:
        return None
    return (m.group(1) + (" " + m.group(2) if m.group(2) else "")).upper()


def product_of(title):
    """'15-inch MacBook Air Apple M4 chip with ...' -> '15-inch MacBook Air'."""
    s = title
    for sep in (" Apple ", " (", ",", " GPS", " Wi-Fi", " - "):
        i = s.find(sep)
        if i > 0:
            s = s[:i]
    return s.strip()


def family_of(title):
    """The 'model' for new-model alerts: product name + chip (or generation)."""
    name, chip = product_of(title), chip_of(title)
    gen = re.search(r"\((\d+(?:st|nd|rd|th) Generation)\)", title)
    if chip:
        name += ", " + chip.replace("PRO", "Pro").replace("MAX", "Max").replace("ULTRA", "Ultra")
    elif gen:
        name += " (" + gen.group(1) + ")"
    return name


def short_title(title):
    s = re.sub(r" Apple ([MA]\d{1,2}(?: Pro| Max| Ultra)?) [Cc]hip with (\d+)-Core CPU and (\d+)-Core GPU",
               r" \1, \2-core CPU / \3-core GPU", title)
    s = re.sub(r" Apple ([MA]\d{1,2}(?: Pro| Max| Ultra)?) [Cc]hip", r" \1", s)
    return s


def item_specs(it, sep=" · "):
    d, out = it.get("d") or {}, []
    tl = it["t"].lower()
    if d.get("tsMemorySize"):
        out.append(d["tsMemorySize"].upper() + " RAM")
    cap = d.get("dimensionCapacity")
    if cap and cap not in tl.replace(" ", ""):
        out.append(cap.upper() + (" SSD" if d.get("tsMemorySize") else ""))
    return sep.join(out)


def link(it, text=None):
    return '<a href="{}">{}</a>'.format(esc(APPLE + it["u"]) if it.get("u") else APPLE,
                                         esc(text or short_title(it["t"])))


def item_block(it, pn, extra=""):
    """Two lines: linked title, then specs · price · part number (· extra)."""
    bits = [b for b in (item_specs(it), "<b>{}</b>".format(money(it["p"])), "<code>{}</code>".format(esc(pn))) if b]
    return link(it) + "\n" + " · ".join(bits) + (" · " + extra if extra else "")


# --------------------------------------------------------------------------------------
# Downloading Apple's pages
# --------------------------------------------------------------------------------------

class Fetcher:
    """Downloads each section at most once per run. get(cat) -> (items, error).
    items = {partNumber: {"t": title, "p": price, "u": url path, "d": filter values}}
    or None when the download failed (a failure never counts as 'sold out')."""

    def __init__(self, fake_dir=None):
        self.cache, self.fake_dir = {}, fake_dir

    def get(self, cat):
        if cat not in self.cache:
            self.cache[cat] = self._load(cat)
        return self.cache[cat]

    def _load(self, cat):
        err = None
        for attempt in range(2):
            if attempt:
                time.sleep(4)
            try:
                if self.fake_dir:
                    with open(os.path.join(self.fake_dir, cat + ".json"), encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    data = self._download(cat)
                return parse_tiles(data), None
            except Exception as e:  # noqa: BLE001 - every problem is a failed download
                err = str(e)[:120] or type(e).__name__
        log("{}: download failed: {}".format(cat, err))
        return None, err

    @staticmethod
    def _download(cat):
        req = urllib.request.Request(PAGE.format(cat), headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-SG,en;q=0.9", "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
        except urllib.error.HTTPError as e:
            raise IOError("HTTP {}".format(e.code))
        except OSError as e:
            raise IOError("network error ({})".format(type(e).__name__))
        page = body.decode("utf-8", "replace")
        i = page.find(MARK)
        if i < 0:
            raise ValueError("the page has no stock list (Apple changed the page?)")
        data, _ = json.JSONDecoder().raw_decode(page, page.find("{", i))
        return data


def parse_tiles(data):
    if not isinstance(data, dict) or "tiles" not in data:
        raise ValueError("the stock list has an unexpected format")
    tiles = data["tiles"]
    if tiles is None:  # what Apple sends for an empty section
        tiles = []
    if not isinstance(tiles, list):
        raise ValueError("the stock list has an unexpected format")
    items, bad = {}, 0
    for t in tiles:
        try:
            pn = str(t["partNumber"]).upper()
            price = float(t["price"]["currentPrice"]["raw_amount"])
            dims = (t.get("filters") or {}).get("dimensions") or {}
            items[pn] = {"t": clean_title(t["title"]), "p": price,
                         "u": str(t.get("productDetailsUrl") or "").split("?")[0],
                         "d": {k: str(v).lower() for k, v in dims.items() if v not in (None, "")}}
        except (KeyError, TypeError, ValueError):
            bad += 1
    if tiles and bad > len(tiles) // 5:
        raise ValueError("{} of {} items had an unexpected format".format(bad, len(tiles)))
    return items


# --------------------------------------------------------------------------------------
# Watches: parsing and matching
# --------------------------------------------------------------------------------------

FULL_PN = re.compile(r"^([FG][A-Z0-9]{4})([A-Z]{1,2})(?:/A)?$", re.I)
SHORT_PN = re.compile(r"^[FG][A-Z0-9]{4}$", re.I)
CHIP_RE = re.compile(r"^([ma]\d{1,2})(pro|max|ultra|bionic)?$")
CHIP_SUFFIX = ("pro", "max", "ultra", "bionic")
CORE_RE = re.compile(r"^(\d{1,2})-?(?:core|cores|c)$")
MEM_RE = re.compile(r"^(\d{1,4})(gb|tb)$")
INCH_RE = re.compile(r'^(\d{1,2}(?:\.\d)?)(?:-?inch(?:es)?|in|")$')
MM_RE = re.compile(r"^(\d{2})mm$")
YEAR_RE = re.compile(r"^20\d\d$")
NUM_RE = re.compile(r"^\d{1,2}(?:\.\d)?$")
PRICE_RE = re.compile(r"^(?:s?\$)?(\d[\d,]*(?:\.\d+)?)(k?)$")
MAX_WORDS = ("max", "under", "below", "budget", "<", "<=", "maxprice", "price")


def part_base(tok):
    m = FULL_PN.match(tok)
    base = m.group(1) if m else tok if SHORT_PN.match(tok) else None
    return base.upper() if base and re.search(r"\d", base) else None


def cat_of_word(tok):
    if tok in CATS:
        return tok, False
    if tok in CAT_ALIASES:
        return CAT_ALIASES[tok], False
    if tok in CAT_WORDS:
        return CAT_WORDS[tok], True
    return None, False


def _price(v):
    m = PRICE_RE.match(v.replace(" ", ""))
    if not m:
        return None
    p = float(m.group(1).replace(",", ""))
    return p * 1000 if m.group(2) else p


def _mem(v):
    v = v.replace(" ", "")
    if re.match(r"^\d+$", v):
        v += "gb"
    return v if MEM_RE.match(v) else None


def new_spec():
    return {"chip": None, "cores": [], "mem": [], "ram": None, "ssd": None, "size": None, "case": None,
            "year": None, "nano": None, "max": None, "model": [], "words": []}


def parse_spec(cat, toks):
    """toks: lower-case words after the section. Returns (spec, errors)."""
    s, errs, i = new_spec(), [], 0
    while i < len(toks):
        t = toks[i].strip(",;")
        nxt = toks[i + 1].strip(",;") if i + 1 < len(toks) else ""
        i += 1
        if not t:
            continue
        m = re.match(r"^([a-z_]+)[=:](.*)$", t)
        if m:
            key, val = m.group(1), m.group(2)
            if not val and nxt:
                val, i = nxt, i + 1
            if key in ("max", "maxprice", "price", "under", "budget"):
                s["max"] = _price(val)
                if s["max"] is None:
                    errs.append("max={}: not a price (e.g. max=1800)".format(val))
            elif key in ("ram", "memory", "mem"):
                s["ram"] = _mem(val) or errs.append("ram={}: use e.g. ram=16gb".format(val))
            elif key in ("ssd", "storage", "disk", "capacity"):
                s["ssd"] = _mem(val) or errs.append("ssd={}: use e.g. ssd=512gb or ssd=1tb".format(val))
            elif key == "nano":
                if val in ("yes", "y", "on", "true", "1"):
                    s["nano"] = "yes"
                elif val in ("no", "n", "off", "false", "0"):
                    s["nano"] = "no"
                elif val in ("any", "either", ""):
                    s["nano"] = None
                else:
                    errs.append("nano={}: use nano=yes, nano=no or nano=any".format(val))
            elif key == "year":
                s["year"] = val if YEAR_RE.match(val) else errs.append("year={}: use e.g. year=2025".format(val))
            elif key in ("size", "screen"):
                v = val.replace('"', "").replace("-inch", "").replace("inch", "")
                if MM_RE.match(v):
                    s["case"] = v
                elif NUM_RE.match(v):
                    s["size"] = v.replace(".", "_") + "inch"
                else:
                    errs.append("size={}: use e.g. size=14 or size=42mm".format(val))
            elif key == "chip":
                c = CHIP_RE.match(val.replace(" ", ""))
                if c:
                    s["chip"] = (c.group(1) + (" " + c.group(2) if c.group(2) else "")).upper()
                else:
                    errs.append("chip={}: use e.g. chip=m4 or chip=m4pro".format(val))
            elif key in ("colour", "color"):
                s["words"] += words(val)
            else:
                errs.append("{}=: unknown setting (I know max, ram, ssd, nano, year, size, chip)".format(key))
            continue
        if t in MAX_WORDS and nxt and _price(nxt) is not None:
            s["max"], i = _price(nxt), i + 1
            continue
        if t.startswith("<") and _price(t.lstrip("<=")) is not None:
            s["max"] = _price(t.lstrip("<="))
            continue
        c = CHIP_RE.match(t)
        if c:
            suffix = c.group(2)
            if not suffix and nxt in CHIP_SUFFIX:
                suffix, i = nxt, i + 1
            s["chip"] = (c.group(1) + (" " + suffix if suffix else "")).upper()
            continue
        c = CORE_RE.match(t)
        if c:
            what = ""
            if nxt in ("cpu", "gpu"):
                what, i = " " + nxt, i + 1
            s["cores"].append("{}-core{}".format(int(c.group(1)), what))
            continue
        c = MEM_RE.match(t)
        if c:
            v = str(int(c.group(1))) + c.group(2)
            if nxt in ("ram", "memory", "unified"):
                s["ram"], i = v, i + 1
            elif nxt in ("ssd", "storage", "disk"):
                s["ssd"], i = v, i + 1
            else:
                s["mem"].append(v)
            continue
        if t in ("nano", "nano-texture", "nanotexture"):
            s["nano"] = "yes"
            continue
        if t in ("no-nano", "nonano", "no-nano-texture"):
            s["nano"] = "no"
            continue
        c = INCH_RE.match(t)
        if c:
            s["size"] = c.group(1).replace(".", "_") + "inch"
            continue
        if MM_RE.match(t):
            s["case"] = t
            continue
        if YEAR_RE.match(t):
            s["year"] = t
            continue
        if NUM_RE.match(t) and cat in ("mac", "ipad"):
            s["size"] = t.replace(".", "_") + "inch"
            continue
        for w in words(t):
            (s["model"] if w in PRODUCT_WORDS else s["words"]).append(w)
    s["model"] = list(dict.fromkeys(s["model"]))
    s["words"] = list(dict.fromkeys(s["words"]))
    return s, [e for e in errs if e]


def spec_is_empty(s):
    return not any(v for v in s.values())


def describe(w):
    if w["kind"] == "part":
        name = w.get("title")
        return "part <code>{}</code>{}".format(esc(w.get("pn") or w["part"]),
                                              " " + esc(short_title(name)) if name else "")
    head = CAT_NAMES.get(w["cat"], w["cat"])
    if w["kind"] == "cat":
        return "everything in " + head
    s, bits = w["spec"], [head]
    bits += s["model"]
    if s["size"]:
        bits.append(s["size"].replace("inch", "-inch").replace("_", "."))
    if s["case"]:
        bits.append(s["case"])
    if s["chip"]:
        bits.append(s["chip"].replace("PRO", "Pro").replace("MAX", "Max").replace("ULTRA", "Ultra"))
    bits += s["cores"]
    if s["ram"]:
        bits.append(s["ram"].upper() + " RAM")
    if s["ssd"]:
        bits.append(s["ssd"].upper() + " SSD")
    bits += [m.upper() for m in s["mem"]]
    if s["year"]:
        bits.append(s["year"])
    bits += s["words"]
    if s["nano"]:
        bits.append("nano-texture" if s["nano"] == "yes" else "no nano-texture")
    if s["max"] is not None:
        bits.append("max " + money(s["max"]))
    return esc(" · ".join(bits))


def item_words(it):
    ws = set(words(it["t"]))
    ws |= {v.replace("_", "") for v in (it.get("d") or {}).values()}
    for a, b in SYNONYMS:
        if a in ws:
            ws.add(b)
        if b in ws:
            ws.add(a)
    return ws


def spec_match(s, it, price=None):
    d, tl = it.get("d") or {}, it["t"].lower()
    p = it["p"] if price is None else price
    if s["max"] is not None and p > s["max"] + 1e-9:
        return False
    if s["chip"] and chip_of(it["t"]) != s["chip"]:
        return False
    for c in s["cores"]:
        if not re.search(r"\b" + re.escape(c), tl):
            return False
    if s["ram"] and d.get("tsMemorySize") != s["ram"]:
        return False
    if s["ssd"] and d.get("dimensionCapacity") != s["ssd"]:
        return False
    for m in s["mem"]:
        if m not in (d.get("tsMemorySize"), d.get("dimensionCapacity")):
            return False
    if s["size"] and d.get("dimensionScreensize") != s["size"]:
        return False
    if s["case"] and d.get("dimensionCaseSize") != s["case"]:
        return False
    if s["year"] and d.get("dimensionRelYear") != s["year"]:
        return False
    if s["nano"] and ("nano-texture" in tl) != (s["nano"] == "yes"):
        return False
    if s["model"] and not set(s["model"]) <= set(words(product_of(it["t"]))):
        return False
    if s["words"] and not set(s["words"]) <= item_words(it):
        return False
    return True


def watch_match(w, cat, pn, it, price=None):
    if w["kind"] == "part":
        return pn[:5] == w["part"]
    if w["cat"] != cat:
        return False
    return w["kind"] == "cat" or spec_match(w["spec"], it, price)


def matching_watches(state, cat, pn, it, price=None):
    return [n for n, w in enumerate(state["watches"], 1) if watch_match(w, cat, pn, it, price)]


def watch_refs(nums):
    return ("watch " if len(nums) == 1 else "watches ") + ", ".join("#{}".format(n) for n in nums)


def needed_cats(state):
    cats = set()
    for w in state["watches"]:
        if w.get("cat"):
            cats.add(w["cat"])
        else:  # part number whose section isn't known yet
            cats.update(CATS)
    return [c for c in CATS if c in cats]


def section_watched(state, cat):
    """A section with a section or spec watch (part-number watches don't count)."""
    return any(w.get("cat") == cat and w["kind"] != "part" for w in state["watches"])


# --------------------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------------------

def _clean_secret(v):
    v = (v or "").strip().strip("'\"").strip()
    return v[3:] if re.match(r"^bot\d+:", v) else v


class Telegram:
    def __init__(self, token, chat_id, dry):
        self.token, self.chat_id, self.dry = _clean_secret(token), _clean_secret(chat_id), dry
        self.sent = self.failed = 0
        self.outbox = []  # dry run: (silent, text)

    def call(self, method, payload):
        req = urllib.request.Request("https://api.telegram.org/bot{}/{}".format(self.token, method),
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except ValueError:
                return {"ok": False, "description": "HTTP {}".format(e.code)}
        except OSError as e:  # never print the URL: it contains the token
            return {"ok": False, "description": "network error ({})".format(type(e).__name__)}

    def send(self, text, silent=False):
        if self.dry:
            self.outbox.append((silent, text))
            log("----- message{} -----\n{}\n".format(" (silent)" if silent else "", text))
            return True
        ok = True
        for chunk in split_message(text):
            payload = {"chat_id": self.chat_id, "text": chunk, "parse_mode": "HTML",
                       "disable_web_page_preview": True, "disable_notification": silent}
            d = self.call("sendMessage", payload)
            if not d.get("ok") and "parse entities" in str(d.get("description")):
                payload["text"] = html.unescape(re.sub(r"<[^>]+>", "", chunk))
                payload.pop("parse_mode")
                d = self.call("sendMessage", payload)
            if not d.get("ok") and "network error" in str(d.get("description")):
                time.sleep(3)
                d = self.call("sendMessage", payload)
            if d.get("ok"):
                self.sent += 1
            else:
                # Telegram's error text never contains the chat ID or the message
                log("Telegram send failed: {}".format(str(d.get("description"))[:120]))
                self.failed += 1
                ok = False
        return ok

    def updates(self, offset):
        payload = {"timeout": 0, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        d = self.call("getUpdates", payload)
        if not d.get("ok"):
            raise IOError(str(d.get("description") or "getUpdates failed")[:120])
        return d.get("result") or []


def split_message(text, limit=4000):
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            parts.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    return parts + [cur] if cur else parts


MENU = [("stock", "What's in stock (e.g. /stock mac air)"), ("watch", "Watch a section, specs or part number"),
        ("list", "Your watches"), ("unwatch", "Stop a watch (/unwatch 2)"),
        ("settings", "Alerts, quiet hours, daily summary"), ("status", "What the last check saw"),
        ("help", "All commands")]


# --------------------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------------------

def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st, first = json.load(f), False
    except FileNotFoundError:
        st, first = {}, True
    except ValueError:
        log("state.json is unreadable; starting fresh")
        st, first = {}, True
    st.setdefault("version", 1)
    st.setdefault("telegram_offset", None)
    st.setdefault("watches", [])
    st["settings"] = dict(DEFAULT_SETTINGS, **(st.get("settings") or {}))
    for k in ("snap", "families", "gone", "suspect"):
        st.setdefault(k, {})
    st.setdefault("health", {"since": None, "alerted": False})
    st.setdefault("daily", {"last": None, "counts": {}})
    st.setdefault("last_list", [])
    st.setdefault("menu", 0)
    return st, first


def dump_state(st):
    return json.dumps(st, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def save_state(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def count(state, key, n=1):
    c = state["daily"].setdefault("counts", {})
    c[key] = c.get(key, 0) + n


def quiet_now(settings, now):
    q = settings.get("quiet")
    if not q:
        return False
    h = now.astimezone(SGT).hour
    a, b = q
    return a <= h < b if a < b else (h >= a or h < b)


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------

def find_part(fetcher, base, cats):
    for cat in cats:
        items, _ = fetcher.get(cat)
        for pn, it in (items or {}).items():
            if pn[:5] == base:
                return cat, pn, it
    return None


def current_matches(state, fetcher, w):
    """[(pn, item)] in stock now for a watch, cheapest first; None if its section failed."""
    cats = [w["cat"]] if w.get("cat") else (list(CATS) if w["kind"] == "part" else [])
    out, failed = {}, False
    for cat in cats:
        items, _ = fetcher.get(cat)
        if items is None:
            failed = True
            continue
        for pn, it in items.items():
            if watch_match(w, cat, pn, it):
                out[pn] = it
    if failed and not out:
        return None
    return sorted(out.items(), key=lambda x: x[1]["p"])


def unknown_words(spec, items):
    """Words of a spec that no current item has (likely typos)."""
    if not items:
        return []
    all_words = set()
    for it in items.values():
        all_words |= item_words(it)
    return [w for w in spec["model"] + spec["words"] if w not in all_words]


def same_watch(a, b):
    keys = ("kind", "cat", "part", "spec")
    return all(a.get(k) == b.get(k) for k in keys if k != "cat" or a["kind"] != "part")


def add_watch(state, w):
    for n, old in enumerate(state["watches"], 1):
        if same_watch(old, w):
            return n, False
    w["added"] = iso(now_utc())
    state["watches"].append(w)
    return len(state["watches"]), True


def cmd_watch(state, fetcher, toks):
    if not toks:
        return WATCH_HELP
    if len(state["watches"]) >= MAX_WATCHES:
        return "You have {} watches, the most I keep. Remove some with /unwatch first.".format(MAX_WATCHES)
    # 1) numbers from the last /stock list
    if all(re.match(r"^#?\d+$", t) for t in toks):
        lst = state.get("last_list") or []
        if not lst:
            return "Numbers refer to your last /stock list, and you haven't asked for one yet. Try <code>/stock mac</code>."
        replies = []
        for t in toks:
            n = int(t.lstrip("#"))
            if not 1 <= n <= len(lst):
                replies.append("#{}: your last /stock list has items 1 to {}.".format(n, len(lst)))
                continue
            cat, pn = lst[n - 1]
            replies.append(watch_part(state, fetcher, pn[:5], pn, [cat]))
        return "\n\n".join(replies)
    # 2) part numbers (optionally with a section)
    parts = [(t, part_base(t)) for t in toks]
    if any(b for _, b in parts):
        non = [t for t, b in parts if not b]
        hint = [cat_of_word(t)[0] for t in non]
        others = [t for t, c in zip(non, hint) if not c]
        if others:
            return ("A part number can only be combined with a section name, not with <code>{}</code>. "
                    "Send the specs as a separate /watch.".format(esc(" ".join(others))))
        cats = [c for c in hint if c] or None
        return "\n\n".join(watch_part(state, fetcher, b, t.upper() if t.endswith("/a") else None, cats)
                           for t, b in parts if b)
    # 3) a section, with or without specs
    cat, keep = cat_of_word(toks[0])
    rest = toks[1:] if cat and not keep else toks
    if not cat:
        for t in toks:
            cat, keep = cat_of_word(t)
            if cat:
                rest = [x for x in toks if x != t or keep]
                break
    if not cat:
        return ("Which section? Start with one of: {}. For example <code>/watch mac {}</code>"
                .format(", ".join(CATS), esc(" ".join(toks))))
    spec, errs = parse_spec(cat, rest)
    if errs:
        return "I didn't understand:\n" + "\n".join("• " + esc(e) for e in errs) + "\n\nNothing was added."
    w = {"kind": "cat", "cat": cat} if spec_is_empty(spec) else {"kind": "spec", "cat": cat, "spec": spec}
    n, new = add_watch(state, w)
    if not new:
        return "You're already watching that (#{}): {}".format(n, describe(w))
    lines = ["✅ <b>Watch #{} added</b>: {}".format(n, describe(w))]
    items, err = fetcher.get(cat)
    if items is None:
        lines.append("I couldn't load Apple's {} page just now ({}), so I can't show what's in stock. "
                     "I'll keep trying.".format(CAT_NAMES[cat], esc(err)))
    else:
        found = sorted(((pn, it) for pn, it in items.items() if watch_match(w, cat, pn, it)),
                       key=lambda x: x[1]["p"])
        if found:
            lines.append("<b>{} in stock now</b>, from {}:".format(len(found), money(found[0][1]["p"])))
            for pn, it in found[:5]:
                lines.append("• " + link(it) + " · " + " · ".join(
                    b for b in (item_specs(it), "<b>{}</b>".format(money(it["p"]))) if b))
            if len(found) > 5:
                lines.append("…and {} more (see /list or /stock).".format(len(found) - 5))
        else:
            lines.append("Nothing matches right now.")
            bad = unknown_words(w.get("spec") or new_spec(), items)
            if bad:
                lines.append("Note: no {} item has the word(s) <code>{}</code> right now. If that's a typo, "
                             "remove it with <code>/unwatch {}</code> (/stock {} shows Apple's titles)."
                             .format(CAT_NAMES[cat], esc(" ".join(bad)), n, cat))
    lines.append("I'll tell you when a matching item comes into stock, changes price or sells out.")
    return "\n".join(lines)


def watch_part(state, fetcher, base, full, cats):
    found = find_part(fetcher, base, cats or CATS)
    gone = next(((pn, g) for pn, g in state["gone"].items() if pn[:5] == base), None)
    w = {"kind": "part", "part": base, "pn": full or base, "cat": None, "title": None}
    if found:
        cat, pn, it = found
        w.update(cat=cat, pn=pn, title=it["t"])
    elif gone:
        w.update(cat=gone[1].get("cat"), pn=gone[0], title=gone[1]["t"])
    elif cats and len(cats) == 1:
        w["cat"] = cats[0]
    n, new = add_watch(state, w)
    if not new:
        return "You're already watching that (#{}): {}".format(n, describe(state["watches"][n - 1]))
    head = "✅ <b>Watch #{} added</b>: {}".format(n, describe(w))
    if found:
        return head + "\n<b>In stock now</b>:\n" + item_block(found[2], found[1])
    if gone:
        since = parse_iso(gone[1].get("since"))
        return head + "\nNot in stock: sold out {} ago (last price {}). I'll tell you when it's back.".format(
            fmt_dur((now_utc() - since).total_seconds()) if since else "a while", money(gone[1]["p"]))
    failed = [c for c in (cats or CATS) if fetcher.get(c)[0] is None]
    where = ("the {} section".format(CAT_NAMES[w["cat"]]) if w["cat"]
             else "all {} sections every run until it shows up (add the section to check only one, "
                  "e.g. <code>/watch {} ipad</code>)".format(len(CATS), esc(base)))
    return (head + "\n⚠️ It isn't on the store now, so I can't confirm this part number exists"
            + (" (some pages didn't load: {})".format(", ".join(failed)) if failed else "")
            + ". Watching it anyway: I'll check " + where + ".")


def cmd_unwatch(state, toks):
    ws = state["watches"]
    if not toks:
        return "Which one? e.g. <code>/unwatch 2</code> (numbers from /list), or <code>/unwatch all</code>."
    if toks == ["all"]:
        n = len(ws)
        ws.clear()
        return "Removed all {} watches.".format(n) if n else "You have no watches."
    nums = []
    for t in toks:
        if not re.match(r"^#?\d+$", t) or not 1 <= int(t.lstrip("#")) <= len(ws):
            return "<code>{}</code> isn't a watch number. /list shows them (1 to {}).".format(esc(t), len(ws))
        nums.append(int(t.lstrip("#")))
    gone = [describe(ws[n - 1]) for n in sorted(set(nums))]
    for n in sorted(set(nums), reverse=True):
        del ws[n - 1]
    return "Removed:\n" + "\n".join("• " + g for g in gone) + ("\n\nThe others are renumbered: see /list." if ws else "")


def cmd_list(state, fetcher):
    ws = state["watches"]
    if not ws:
        return "You have no watches. Try <code>/stock mac</code> or <code>/watch mac air m4</code>."
    lines = ["👀 <b>Your watches</b> ({})".format(len(ws))]
    for n, w in enumerate(ws, 1):
        lines.append("{}. {}".format(n, watch_status(state, fetcher, w)))
    lines.append("\n/unwatch N removes one. /stock shows everything in a section.")
    return "\n".join(lines)


def watch_status(state, fetcher, w):
    """'🔎 description' + a line with what's in stock now (for /list and the daily summary)."""
    icon = {"cat": "📦", "spec": "🔎", "part": "🏷"}[w["kind"]]
    found = current_matches(state, fetcher, w)
    if found is None:
        now = "couldn't load Apple's page"
    elif not found:
        now = "none in stock"
        if w["kind"] == "part" and not w.get("title"):
            now += " (never seen it, can't confirm it exists)"
    elif w["kind"] == "part":
        now = "in stock, " + money(found[0][1]["p"])
    else:
        now = "<b>{}</b> in stock, from {}".format(len(found), money(found[0][1]["p"]))
    out = "{} {}\n     {}".format(icon, describe(w), now)
    if found and w["kind"] == "spec":
        out += "\n     cheapest: " + link(found[0][1])
    return out


def cmd_stock(state, fetcher, toks):
    page = 1
    if len(toks) >= 2 and toks[-2] in ("page", "p") and toks[-1].isdigit():
        page, toks = int(toks[-1]), toks[:-2]
    elif toks and re.match(r"^p\d+$", toks[-1]):
        page, toks = int(toks[-1][1:]), toks[:-1]
    if not toks:
        lines = ["🛒 <b>Apple SG refurbished store</b> (items per section)"]
        for cat in CATS:
            items, err = fetcher.get(cat)
            lines.append("• <code>{}</code> {}: {}".format(
                cat, CAT_NAMES[cat], "couldn't load ({})".format(esc(err)) if items is None else len(items)))
        lines.append("\nSend <code>/stock mac</code> for the list, or add words to narrow it: "
                     "<code>/stock mac air m4 16gb</code>.")
        return "\n".join(lines)
    cat, keep = cat_of_word(toks[0])
    if not cat:
        return "Start with a section: {}. e.g. <code>/stock mac air</code>".format(", ".join(CATS))
    spec, errs = parse_spec(cat, toks if keep else toks[1:])
    if errs:
        return "I didn't understand:\n" + "\n".join("• " + esc(e) for e in errs)
    items, err = fetcher.get(cat)
    if items is None:
        return "I couldn't load Apple's {} page just now ({}). Try again in a few minutes.".format(
            CAT_NAMES[cat], esc(err))
    found = sorted(((pn, it) for pn, it in items.items() if spec_match(spec, it)), key=lambda x: (x[1]["p"], x[0]))
    what = describe({"kind": "spec", "cat": cat, "spec": spec})
    if not found:
        bad = unknown_words(spec, items)
        return "No {} items match{}.".format(esc(CAT_NAMES[cat]), "" if not bad else
                                             " (no item has <code>{}</code> in its title)".format(esc(" ".join(bad))))
    pages = (len(found) + STOCK_PAGE - 1) // STOCK_PAGE
    page = min(max(page, 1), pages)
    state["last_list"] = [[cat, pn] for pn, _ in found]
    start = (page - 1) * STOCK_PAGE
    lines = ["🛒 <b>{}</b>: {} item{}{}".format(what, len(found),
                                                "s" if len(found) != 1 else "",
                                                ", page {} of {}".format(page, pages) if pages > 1 else "")]
    for n, (pn, it) in enumerate(found[start:start + STOCK_PAGE], start + 1):
        spec_txt = item_specs(it, "/")
        lines.append("<b>{}.</b> {} · {}{} · <code>{}</code>".format(
            n, money(it["p"]), link(it), " · " + spec_txt if spec_txt else "", esc(pn)))
    if page < pages:
        lines.append("\nNext: <code>/stock {} page {}</code>, or add words to narrow it down.".format(
            esc(" ".join(toks)), page + 1))
    lines.append("<code>/watch N</code> watches item N (exact part number).")
    return "\n".join(lines)


SETTING_ALIASES = {"restock": "restock", "restocks": "restock", "instock": "restock",
                   "price": "price", "prices": "price", "soldout": "soldout", "sold-out": "soldout",
                   "sold": "soldout", "daily": "daily", "summary": "daily", "health": "health",
                   "newmodel": "newmodel", "new-model": "newmodel", "newmodels": "newmodel", "new": "newmodel",
                   "quiet": "quiet", "mingone": "min_gone", "min_gone": "min_gone", "gone": "min_gone"}
ON = ("on", "yes", "true", "1")
OFF = ("off", "no", "false", "0", "none")


def settings_text(s):
    q = s["quiet"]
    return "\n".join([
        "⚙️ <b>Settings</b>",
        "<code>restock</code> {}: item comes into stock (with sound)".format("on" if s["restock"] else "off"),
        "<code>price</code> {}: price changes (on = any change, drops = only drops)".format(s["price"]),
        "<code>soldout</code> {}: item sells out (silent)".format("on" if s["soldout"] else "off"),
        "<code>newmodel</code> {}: a model appears for the first time in a section you watch".format(
            "on" if s["newmodel"] else "off"),
        "<code>health</code> {}: Apple's page fails for {} min, then when it works again".format(
            "on" if s["health"] else "off", HEALTH_AFTER_MIN),
        "<code>daily</code> {}: silent daily summary (Singapore time)".format(
            "{:02d}:00".format(s["daily"]) if s["daily"] is not None else "off"),
        "<code>quiet</code> {}: alerts arrive silently during these hours".format(
            "{:02d}:00-{:02d}:00".format(*q) if q else "off"),
        "<code>mingone</code> {}: only report sold out / back in stock if the item was gone at least this "
        "long".format("{} min".format(s["min_gone"]) if s["min_gone"] else "off"),
        "",
        "Change: <code>/settings price drops</code>, <code>/settings daily 8</code>, <code>/settings quiet 23-7</code>, "
        "<code>/settings mingone 30</code>, <code>/settings soldout off</code> (several at once is fine)."])


def cmd_settings(state, toks):
    s = state["settings"]
    if not toks:
        return settings_text(s)
    errs, done, i = [], [], 0
    toks = [x for t in toks for x in t.split("=") if x]
    while i < len(toks):
        key = SETTING_ALIASES.get(toks[i])
        val = toks[i + 1] if i + 1 < len(toks) else ""
        i += 2
        if not key:
            errs.append("unknown setting <code>{}</code>".format(esc(toks[i - 2])))
            i -= 1
            continue
        if key in ("restock", "soldout", "health", "newmodel"):
            if val in ON + OFF:
                s[key] = val in ON
                done.append("{} {}".format(key, "on" if s[key] else "off"))
            else:
                errs.append("{}: use on or off".format(key))
        elif key == "price":
            v = {"drop": "drops", "drops": "drops", "down": "drops"}.get(val, val)
            if v in ON + OFF + ("drops",):
                s["price"] = "on" if v in ON else "off" if v in OFF else "drops"
                done.append("price " + s["price"])
            else:
                errs.append("price: use on, drops or off")
        elif key == "daily":
            m = re.match(r"^(\d{1,2})(?::00)?$", val)
            if val in OFF:
                s["daily"] = None
                done.append("daily summary off")
            elif val in ON:
                s["daily"] = DEFAULT_SETTINGS["daily"]
                done.append("daily summary at {:02d}:00".format(s["daily"]))
            elif m and int(m.group(1)) < 24:
                s["daily"] = int(m.group(1))
                done.append("daily summary at {:02d}:00".format(s["daily"]))
            else:
                errs.append("daily: an hour 0-23 (Singapore time), or off")
        elif key == "quiet":
            m = re.match(r"^(\d{1,2})(?::00)?-(\d{1,2})(?::00)?$", val)
            if val in OFF:
                s["quiet"] = None
                done.append("quiet hours off")
            elif m and int(m.group(1)) < 24 and int(m.group(2)) < 24 and m.group(1) != m.group(2):
                s["quiet"] = [int(m.group(1)), int(m.group(2))]
                done.append("quiet {:02d}:00-{:02d}:00".format(*s["quiet"]))
            else:
                errs.append("quiet: hours like 23-7 (Singapore time), or off")
        elif key == "min_gone":
            m = re.match(r"^(\d{1,4})(?:m|min)?$", val)
            if val in OFF:
                s["min_gone"] = 0
                done.append("mingone off")
            elif m:
                s["min_gone"] = int(m.group(1))
                done.append("mingone {} min".format(s["min_gone"]) if s["min_gone"] else "mingone off")
            else:
                errs.append("mingone: minutes, e.g. 30, or off")
    out = []
    if done:
        out.append("✅ " + ", ".join(done))
    if errs:
        out.append("⚠️ " + "\n⚠️ ".join(errs))
    return "\n".join(out) + "\n\n" + settings_text(s)


def cmd_status(state, fetcher):
    lines = ["🩺 <b>Status</b> (checked just now)"]
    cats = needed_cats(state)
    if not cats:
        lines.append("No watches, so I'm not downloading anything.")
    for cat in cats:
        items, err = fetcher.get(cat)
        lines.append("• {}: {}".format(CAT_NAMES[cat], "{} items".format(len(items)) if items is not None
                                       else "failed ({})".format(esc(err))))
    since = parse_iso(state["health"].get("since"))
    if since:
        lines.append("Apple has been failing for {}.".format(fmt_dur((now_utc() - since).total_seconds())))
    lines.append("Watches: {}. Daily summary: {}.".format(
        len(state["watches"]), "{:02d}:00".format(state["settings"]["daily"])
        if state["settings"]["daily"] is not None else "off"))
    return "\n".join(lines)


def process_commands(tg, state, fetcher, fake=None):
    if fake is not None:
        updates = [{"update_id": None, "message": {"chat": {"id": tg.chat_id}, "text": t,
                                                   "date": time.time()}} for t in fake]
    elif tg.dry:
        return
    else:
        try:
            updates = tg.updates(state["telegram_offset"])
        except IOError as e:
            log("Telegram: couldn't read commands: {}".format(e))
            tg.failed += 1
            return
    first_read = state["telegram_offset"] is None
    handled = ignored = 0
    for u in updates:
        if u["update_id"] is not None:
            state["telegram_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != tg.chat_id:
            ignored += 1  # someone else found the bot: no reply, nothing logged
            continue
        if first_read and msg.get("date", time.time()) < time.time() - 2 * 3600:
            continue  # very first read: skip old messages sent before the bot existed
        handled += 1
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            tg.send("Send /help to see what I can do.")
            continue
        parts = text.lower().split()
        cmd, toks = parts[0][1:].split("@")[0], parts[1:]
        try:
            if cmd in ("help", "start"):
                reply = HELP
            elif cmd in ("watch", "track", "add"):
                reply = cmd_watch(state, fetcher, toks)
            elif cmd in ("unwatch", "untrack", "remove", "delete"):
                reply = cmd_unwatch(state, toks)
            elif cmd in ("list", "watches"):
                reply = cmd_list(state, fetcher)
            elif cmd == "stock":
                reply = cmd_stock(state, fetcher, toks)
            elif cmd in ("settings", "setting", "set"):
                reply = cmd_settings(state, toks)
            elif cmd == "status":
                reply = cmd_status(state, fetcher)
            else:
                reply = "Unknown command <code>/{}</code>. Send /help.".format(esc(cmd))
        except Exception as e:  # noqa: BLE001 - one bad command must not stop the alerts
            log("command failed: {}".format(type(e).__name__))
            reply = "⚠️ Something went wrong with that command ({}). The GitHub log has details.".format(
                esc(type(e).__name__))
        tg.send(reply)
    if handled or ignored:
        log("Telegram: {} command(s) answered, {} message(s) from other chats ignored".format(handled, ignored))


# --------------------------------------------------------------------------------------
# The stock check
# --------------------------------------------------------------------------------------

def check(state, fetcher, tg, now):
    """Compare this run's pages with state['snap'] and send alerts."""
    st = state["settings"]
    quiet = quiet_now(st, now)
    cats = needed_cats(state)
    restock, price_up, price_down, soldout, newfam = [], [], [], [], {}
    seen = set()
    failed = []
    min_gone = st["min_gone"] * 60

    for cat in cats:
        items, err = fetcher.get(cat)
        if items is None:
            failed.append((cat, err))
            continue
        prev = state["snap"].get(cat)
        fams = set(state["families"].get(cat) or [])
        if prev is None:  # first time this section is downloaded: remember, don't alert
            state["snap"][cat] = {pn: dict(it, s=None) for pn, it in items.items()}
            state["families"][cat] = sorted({family_of(it["t"]).lower() for it in items.values()})
            log("{}: {} items (first download, remembered)".format(cat, len(items)))
            continue
        shrank = (not items and prev) or (len(prev) >= 10 and len(items) < len(prev) / 2)
        if shrank and not state["suspect"].get(cat):
            state["suspect"][cat] = 1  # could be a glitch: believe it only if the next run agrees
            log("{}: {} items, was {}: waiting for the next run to confirm".format(cat, len(items), len(prev)))
            continue
        state["suspect"].pop(cat, None)
        new_snap = {}
        added = removed = changed = 0
        for pn, it in items.items():
            old = prev.get(pn)
            new_snap[pn] = dict(it, s=old.get("s") if old else iso(now))
            if old is None:
                added += 1
                g = state["gone"].pop(pn, None)
                gone_for = (now - parse_iso(g["since"])).total_seconds() if g and parse_iso(g.get("since")) else None
                flicker = gone_for is not None and gone_for < min_gone
                if flicker:  # only briefly gone: keep its original "in stock since"
                    new_snap[pn]["s"] = g.get("s")
                for w in state["watches"]:  # a part watch that hadn't found its section yet
                    if w["kind"] == "part" and w["part"] == pn[:5] and not w.get("cat"):
                        w.update(cat=cat, pn=pn, title=it["t"])
                if pn in seen:
                    continue
                seen.add(pn)
                fam = family_of(it["t"])
                is_new_fam = fam.lower() not in fams
                if is_new_fam and section_watched(state, cat):
                    newfam.setdefault((cat, fam), []).append((pn, it))
                if flicker:
                    continue  # no alerts either way
                nums = matching_watches(state, cat, pn, it)
                if nums:
                    count(state, "restock")
                    if st["restock"]:
                        restock.append((pn, it, nums, g, gone_for, is_new_fam))
            elif abs(old["p"] - it["p"]) > 0.004:
                changed += 1
                if pn in seen:
                    continue
                seen.add(pn)
                nums = sorted(set(matching_watches(state, cat, pn, it, old["p"])
                                  + matching_watches(state, cat, pn, it)))
                if nums:
                    count(state, "price")
                    if st["price"] == "on" or (st["price"] == "drops" and it["p"] < old["p"]):
                        (price_down if it["p"] < old["p"] else price_up).append((pn, it, old["p"], nums))
        for pn, old in prev.items():
            if pn not in items:
                removed += 1
                if pn not in state["gone"]:
                    state["gone"][pn] = dict(old, cat=cat, since=iso(now), done=False)
        state["snap"][cat] = new_snap
        state["families"][cat] = sorted(fams | {family_of(it["t"]).lower() for it in items.values()})
        log("{}: {} items ({} new, {} gone, {} price changes)".format(cat, len(items), added, removed, changed))

    # sold out: once the item has been gone min_gone minutes (immediately when off)
    for pn, g in list(state["gone"].items()):
        since = parse_iso(g.get("since"))
        if not since or (now - since).days > GONE_KEEP_DAYS or g.get("cat") not in cats:
            del state["gone"][pn]
            continue
        if g.get("done") or (now - since).total_seconds() < min_gone or g["cat"] in dict(failed):
            continue
        g["done"] = True
        nums = matching_watches(state, g["cat"], pn, g)
        if nums and pn not in seen:
            seen.add(pn)
            count(state, "soldout")
            if st["soldout"]:
                first = parse_iso(g.get("s"))
                soldout.append((pn, g, nums, (since - first).total_seconds() if first else None))

    # ---- messages ----
    if restock:
        lines = ["🟢 <b>{}</b>".format("In stock" if len(restock) == 1 else "{} items in stock".format(len(restock)))]
        for pn, it, nums, g, gone_for, is_new in sorted(restock, key=lambda x: x[1]["p"]):
            notes = [watch_refs(nums)]
            if is_new:
                notes.append("🆕 new model")
            if g:
                notes.append("back after {}".format(fmt_dur(gone_for)) if gone_for is not None else "back in stock")
                if abs(g["p"] - it["p"]) > 0.004:
                    notes.append("was {}".format(money(g["p"])))
            else:
                notes.append("new listing")
            lines.append("\n" + item_block(it, pn) + "\n<i>" + esc(" · ".join(notes)) + "</i>")
        tg.send("\n".join(lines), silent=quiet)
    for group, icon, word in ((price_down, "📉", "Price drop"), (price_up, "📈", "Price up")):
        if not group:
            continue
        lines = ["{} <b>{}</b>".format(icon, word if len(group) == 1 else "{}s ({})".format(word, len(group)))]
        for pn, it, old, nums in group:
            diff = it["p"] - old
            lines.append("\n{} → <b>{}</b> ({}{})\n{}\n<i>{}</i>".format(
                money(old), money(it["p"]), "+" if diff > 0 else "−", money(abs(diff)),
                item_block(it, pn), esc(watch_refs(nums))))
        tg.send("\n".join(lines), silent=quiet or bool(group is price_up))
    if soldout:
        lines = ["⚪ <b>Sold out</b>" + (" ({})".format(len(soldout)) if len(soldout) > 1 else "")]
        for pn, g, nums, held in soldout:
            note = watch_refs(nums) + (" · was in stock {}".format(fmt_dur(held)) if held else "")
            lines.append("\n" + item_block(g, pn) + "\n<i>" + esc(note) + "</i>")
        tg.send("\n".join(lines), silent=True)
    alerted = {pn for pn, *_ in restock}
    fam_lines = []
    for (cat, fam), its in sorted(newfam.items()):
        rest = [(pn, it) for pn, it in its if pn not in alerted]
        if not rest:
            continue
        rest.sort(key=lambda x: x[1]["p"])
        fam_lines.append("\n<b>{}</b> ({}): {} config{} from {}\n".format(
            esc(fam), CAT_NAMES[cat], len(rest), "s" if len(rest) > 1 else "", money(rest[0][1]["p"]))
            + "\n".join("• " + link(it) + " · " + money(it["p"]) for pn, it in rest[:5]))
    if fam_lines and st["newmodel"]:
        tg.send("🆕 <b>New model in the refurbished store</b>" + "".join(fam_lines)
                + "\n\n<i>First time I've seen this model in a section you watch. Use /watch to get "
                  "alerts for it.</i>", silent=quiet)
    return failed


def health(state, tg, failed, now):
    h, st = state["health"], state["settings"]
    if failed:
        count(state, "failed_runs")
        if not h.get("since"):
            h["since"], h["alerted"] = iso(now), False
        since = parse_iso(h["since"]) or now
        if not h["alerted"] and (now - since).total_seconds() >= HEALTH_AFTER_MIN * 60:
            h["alerted"] = True
            if st["health"]:
                tg.send("⚠️ <b>I can't read Apple's store</b> (for {} now).\n{}\nStock alerts are paused "
                        "for these sections; nothing is counted as sold out. I'll tell you when it works again."
                        .format(fmt_dur((now - since).total_seconds()),
                                "\n".join("• {}: {}".format(CAT_NAMES[c], esc(e)) for c, e in failed)),
                        silent=quiet_now(st, now))
    elif h.get("since"):
        since = parse_iso(h["since"]) or now
        if h.get("alerted") and st["health"]:
            tg.send("✅ <b>Apple's store is loading again</b> (it failed for {}). Alerts are back on.".format(
                fmt_dur((now - since).total_seconds())), silent=quiet_now(st, now))
        h["since"], h["alerted"] = None, False


def daily(state, tg, fetcher, now, first_run):
    st, d = state["settings"], state["daily"]
    local = now.astimezone(SGT)
    today = local.strftime("%Y-%m-%d")
    if st["daily"] is None or local.hour < st["daily"] or d.get("last") == today:
        return
    d["last"] = today
    c, d["counts"] = d.get("counts") or {}, {}
    if first_run or not state["watches"]:
        return
    lines = ["☀️ <b>Daily summary</b> · {} {} {}".format(local.strftime("%a"), local.day, local.strftime("%b"))]
    lines.append("Since the last summary, items you watch: {} came into stock, {}, {} sold out.".format(
        c.get("restock", 0), plural(c.get("price", 0), "price change"), c.get("soldout", 0)))
    for n, w in enumerate(state["watches"], 1):
        lines.append("{}. {}".format(n, watch_status(state, fetcher, w)))
    fails = c.get("failed_runs", 0)
    lines.append("\n" + ("Apple's store loaded fine on every check." if not fails else
                         "⚠️ {} couldn't load Apple's store.".format(plural(fails, "check"))))
    tg.send("\n".join(lines), silent=True)


def prune(state):
    """Forget sections nobody watches any more (keeps state.json small)."""
    keep = set(needed_cats(state))
    for k in ("snap", "families", "suspect"):
        for cat in list(state[k]):
            if cat not in keep:
                del state[k][cat]


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------

def main():
    dry = os.environ.get("DRY_RUN") == "1"
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not dry and (not _clean_secret(token) or not _clean_secret(chat)):
        log("The TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets must both be set (SETUP.md, step 4).")
        return 1
    tg = Telegram(token, chat or "0", dry)
    fake = os.environ.get("FAKE_COMMANDS")
    fetcher = Fetcher(os.environ.get("FAKE_DATA_DIR") or None)
    state, first_run = load_state(STATE_FILE)
    before = dump_state(state)
    now = now_utc()

    if first_run:
        tg.send("✅ <b>Apple refurbished bot is running.</b>\nI check Apple Singapore's refurbished store "
                "every 5 minutes for the sections you watch.\nStart with <code>/stock mac</code> or "
                "<code>/watch mac air m4</code>. /help lists everything.")
    if not dry and state.get("menu") != MENU_VERSION:
        d = tg.call("setMyCommands", {"commands": [{"command": c, "description": t} for c, t in MENU]})
        if d.get("ok"):
            state["menu"] = MENU_VERSION

    process_commands(tg, state, fetcher, fake.split("\n") if fake else None)
    failed = check(state, fetcher, tg, now)
    health(state, tg, failed, now)
    daily(state, tg, fetcher, now, first_run)
    if os.environ.get("SEND_STATUS") == "1":
        tg.send(cmd_status(state, fetcher) + "\n\n<i>(manual run)</i>", silent=True)
    prune(state)

    after = dump_state(state)
    if after != before or first_run:
        save_state(STATE_FILE, after)
        log("state.json updated")
    else:
        log("nothing changed")
    if tg.failed:
        log("{} Telegram request(s) failed".format(tg.failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
