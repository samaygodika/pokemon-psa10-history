#!/usr/bin/env python3
"""alt.xyz card -> TCGplayer product id (and card image) -> latest/tcgplayer_ids.csv

PokeSniper pays PokemonPriceTracker (PPT) for card identity and images. PPT's
records are TCGplayer's catalog: its `tcgPlayerId` is TCGplayer's product id
(378 of the 382 PPT ids in PokeSniper's catch-all export are in it, 374 with the
same name) and its `imageCdnUrl` is TCGplayer's public image CDN. tcgcsv.com
republishes that catalog free every day, so this joins the feed's English cards
to it directly: with the feed as the card list, the app needs no PPT call for a
card or an image.

Matching, per English TCG row (coverage.english_tcg, the filter the app uses).
No image beats the wrong one, so anything unclear is left out.
  0. skipped: products TCGplayer doesn't sell as singles (Topps Chrome, playing
     cards, vending cards...) and Japanese-only sets alt.xyz doesn't label
     Japanese (names from TCGplayer's own Japanese catalog). Both number their
     cards by Pokedex number, so they'd otherwise "match" SV 151.
  1. candidates: TCGplayer singles with the same card number (letters kept,
     leading zeros and "/total" dropped; a promo also by its bare number, as
     alt.xyz writes "#075" for SWSH075) whose name agrees with alt's subject: the
     first 6 letters of any '/'-part against any '&'-part, Mega/M and regional
     prefixes aside, never two different species (Mew / Mewtwo). A card alt.xyz
     gives no number takes only an exact name TCGplayer has <= 3 products for.
  2. a candidate whose bracketed qualifier alt's name spells out wins outright
     ("Wartortle - 50/112 (Prerelease)", "(Poke Ball Pattern)"; whole-set variants:
     Prize Pack = "Play!", Jumbo, Trick or Trade, McDonald's). A bracket that is just
     a set name ("(XY Steam Siege)", a theme-deck reprint) doesn't count. A row whose
     name says special print (stamp, prerelease, league, Cosmos...) and has no
     such candidate gets nothing.
  3. otherwise a candidate must fit the year and its set must have alt's set's
     vote. Year: within the years of the alt cards the set confidently matches
     (+/- 1; learned in a first pass, since TCGplayer leaves some old sets undated
     and a promo set's date is only where it starts), else its release year (+/- 1;
     a promo set: released no later than a year after the card). Vote: alt's set
     (year + set + variety, and year + set) votes for the TCGplayer sets its cards'
     candidates are in; >= 25% of it, or >= 10% from 3+ cards. An alt set too small
     to vote (< 3 rows) relies on the year, except in an undated set.
  4. several sets left: the one alt's '/(...)' note names; else the best-voted if
     it has 2x the next; else Base Set's rule (1st Edition and Shadowless are in
     "Base Set (Shadowless)"); else the set name sharing most words with alt's.
     Within one set, variants sharing a number go by alt's words ("Energy Reverse"
     -> "(Energy Symbol Pattern)"), else the plain card.

Checked 2026-10-01 against the 28 PPT-id pairs in PokeSniper's catch-all export
(22 same, 5 refused, 1 differs where alt.xyz's name now says "Winner") and
against PokeSniper's own mapper run over the same TCGplayer records (same id on
12,621 of 12,736 shared cards; the rest: alt's name names a ball pattern or
special print that mapper gives the plain card, or a jumbo / Prize Pack / World
Championship deck product it links to a regular card).

  asset_id          alt.xyz asset id (latest/cards.csv); a card with no row has no match
  tcgplayer_id      TCGplayer product id (= PPT's tcgPlayerId)
  image_url         https://tcgplayer-cdn.tcgplayer.com/product/<id>_in_800x800.jpg
                    (PPT's imageCdnUrl; _in_200x200 / _in_1000x1000 / _200w also exist)
  tcgplayer_name, tcgplayer_set, tcgplayer_number, tcgplayer_rarity   as TCGplayer lists them
                    (PPT's name, setName, cardNumber, rarity are the same strings)
  match             unique | best_set | named_variant | variant | plain_variant

The same run writes two files PokeSniper builds its card list from (2026-10-02, Sid: the feed
as the master card list, no PPT):

latest/card_catalog.csv, one row per card: every English TCG row of latest/cards.csv, plus
every TCGplayer English single no row is matched to.
  asset_id          alt.xyz asset id; blank for a TCGplayer-only card
  tcgplayer_id      blank for an alt.xyz-only card
  status            linked          matched (tcgplayer_ids.csv has the same pair)
                    alt_only        a real English card TCGplayer has no product for, or one this
                                    script won't guess between (`how` says which)
                    not_english     alt.xyz labels it English but it isn't an English TCG card
                                    (Japanese / Chinese sets and promos, Topps movie cards, ...)
                    tcgplayer_only  a TCGplayer single with no alt.xyz row
  how               linked: the match kind; alt_only: no_candidate | variant_not_found |
                    set_not_aligned | ambiguous_set | ambiguous_variant; not_english:
                    not_on_tcgplayer | not_english_tcg; tcgplayer_only: blank, or
                    candidate_of_alt_row when an unmatched alt.xyz row might be this card
  name, number      TCGplayer's (linked, tcgplayer_only); alt.xyz's subject and number otherwise
  set, set_source   TCGplayer's set name (tcgplayer); for an alt.xyz-only card the set its
                    candidates are all in (candidates) or its alt.xyz set voted for (voted), else blank
  rarity            TCGplayer's; blank for an alt.xyz-only card (unknown, not common)
  year              alt.xyz's year for a card it has; else the set's year (tcgplayer_sets.csv),
                    blank for a set spanning many years
  print_run         1st Edition | Shadowless | Reverse Holo | blank, from alt.xyz's name
  The image is IMAGE_URL with the tcgplayer_id (no column: it is the same pattern on every row,
  and leaving it out keeps the file ~2.5 MB smaller); no tcgplayer_id, no image.

latest/tcgplayer_sets.csv, one row per TCGplayer English set: group_id, name, published (blank:
TCGplayer has no date), year, year_source (published | learned: >= 80% of its 5+ matched alt.xyz
cards carry it | manual: see MANUAL_SET_YEARS | multi_year: no one year fits), span_from/span_to
(years of its matched alt.xyz cards), linked_cards, note.

Usage: python3 history/tcgplayer_ids.py [--fetch] [--tcgcsv DIR] [--out FILE] [--report FILE]
                                        [--catalog FILE] [--sets FILE]
  --fetch downloads the catalog (~220 requests, 0.3 s apart) into --tcgcsv first.
"""
import argparse
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coverage import english_tcg  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
FEED = HERE / "latest" / "cards.csv"
CHARACTERS = HERE / "latest" / "characters.csv"   # coverage.py's 469 species: tells Mew from Mewtwo
OUT = HERE / "latest" / "tcgplayer_ids.csv"
TCGCSV_DIR = HERE / "snapshots" / "tcgcsv"        # gitignored; the nightly uses snapshots/<day>/tcgcsv
TCGCSV = "https://tcgcsv.com"
EN, JP = 3, 85                                   # TCGplayer categories: Pokemon, Pokemon Japan
USER_AGENT = "pokemon-psa10-history/1.0 (+https://github.com/samaygodika/pokemon-psa10-history)"
IMAGE_URL = "https://tcgplayer-cdn.tcgplayer.com/product/{}_in_800x800.jpg"
COLS = ["asset_id", "tcgplayer_id", "image_url", "tcgplayer_name", "tcgplayer_set", "tcgplayer_number",
        "tcgplayer_rarity", "match"]

MIN_VOTERS = 3          # rows with candidates an alt set needs before its vote counts
MIN_SHARE = 0.25        # a candidate's TCGplayer set needs this share of the alt set's vote ...
MIN_SHARE_VOTES = (0.10, 3)  # ... or this share from at least this many cards
PROMO_SET = re.compile(r"promo|black star|miscellaneous|deck exclusives|blister|jumbo|prize pack|league|"
                       r"trainer kit|world championship", re.I)
# Not sold on TCGplayer as Pokemon singles, plus Japanese product lines missing from its
# Japanese set names (alt.xyz labels none of these "Japanese").
NOT_ON_TCGPLAYER = re.compile(r"\b(chrome|playing cards?|poker|scratch cards?|vending|game tips|kellogg s|tomy|"
                              r"meiji|melee|advanced challenge|advanced action|pokemon advanced)\b")
# Words in alt's name that mean "a special print of the card" (stamped, store-exclusive,
# Cosmos/Cracked Ice holo...). TCGplayer lists these as their own product, often in another
# set ("Miscellaneous Cards & Products", "Blister Exclusives"), with the variant in brackets.
# 11 of the 28 PPT-id pairs in PokeSniper's catch-all export were these; before this rule
# they matched the plain card.
VARIANT_WORDS = re.compile(r"\b(stamp|stamped|prerelease|pre release|staff|league|championships?|winner|cosmos?|"
                           r"cracked ice|non holo|jumbo|error|test print|position only|e3|toys r us|gamestop|"
                           r"eb games|pokemon center|build a bear|pokemon day|holiday calendar|deck tin)\b")
NOT_A_VARIANT = re.compile(r" gr[ae]y stamp ")   # Base Set 1st Edition's gray-stamp run: TCGplayer's 1st Edition
# Sets where every card is the variant, so its products carry no bracket: a row naming one of
# these word sets ("Stamp [Play! Pokemon]", "Pokeween", "Jumbo") names that set's card.
SET_IMPLIES = [(re.compile(r"^Prize Pack"), ({"play"}, {"prize", "pack"})),
               (re.compile(r"^Jumbo Cards$"), ({"jumbo"}, {"oversize"}, {"oversized"})),
               (re.compile(r"^Trick or Trade"), ({"trick", "trade"}, {"pokeween"}, {"halloween"})),
               (re.compile(r"^McDonald's"), ({"mcdonald"}, {"mcdonalds"}))]
QUALIFIER_STOP = {"pokemon", "pattern", "a", "the", "of", "and", "exclusive", "workshop", "promo"}
GENERIC = {"holo", "reverse", "foil", "rare"}     # too common in alt's names to pick a variant alone
NUM = re.compile(r"^([A-Z]*?)0*(\d+)([A-Z]*)$")
# alt.xyz often drops a promo number's series prefix ("Special Delivery Charizard #075" is
# TCGplayer's SWSH075), so promo-set cards are also found by the bare number
PROMO_PREFIX = re.compile(r"^(SWSH|SM|XY|BW|DP|HGSS|SVP|SV|MEP|ME)0*(\d+)$")

CATALOG = HERE / "latest" / "card_catalog.csv"
SETS = HERE / "latest" / "tcgplayer_sets.csv"
CATALOG_COLS = ["asset_id", "tcgplayer_id", "status", "how", "name", "set", "set_source", "number", "rarity",
                "year", "print_run"]
SETS_COLS = ["group_id", "name", "published", "year", "year_source", "span_from", "span_to", "linked_cards", "note"]
# Rows alt.xyz files as English that aren't English TCG cards, read off the unmatched rows of the
# 2026-10-01 feed (checked only on rows that matched nothing): Japanese sets alt.xyz doesn't label
# Japanese (GX Ultra Shiny, Best of XY, Super-Burst Impact), a Chinese one (Storming Emergence,
# "SSR"), Japanese magazine / gym / store / event promos, and Topps' movie trading cards.
NOT_ENGLISH = re.compile(r" (gx shiny|best pokemon the of xy|best of xy|super burst|storming emergence|ssr|"
                         r"weekly advanced generation|japan championships|pokemon card gym|university festival|"
                         r"card station|daiichi pan|bear walker|happy adventure rally|festa|panini|smart cards|"
                         r"combini|movie animation edition|movie foilboard|cardd?ass|danone|corocoro|mirage pokemon|"
                         r"daisuki club|stamp rally|visual placards|collection file) ")
TOPPS_MOVIE = re.compile(r"^ \d{4} pokemon movie ")      # "2000 Pokemon Movie Three Treasures #52"
# Japanese promo numbering puts the series after the number ("107/XY-P": alt.xyz "#107XYP",
# "#036DPtP", "#052LP"); English promos put it first (SM78, XY41, SWSH050)
JP_PROMO_NUMBER = re.compile(r"^\d*[A-Z]?(SM-?P|XY-?P|BW-?P|DPT?-?P|LP|S-?P|SV-?P)$")
LEARNED_SHARE = 0.8     # an undated set takes the year >= 80% of its matched alt.xyz cards carry
# TCGplayer sets with no release date and too few matched cards to learn one (2026-10-02).
# group_id: (year or None, span_from, span_to, note)
MANUAL_SET_YEARS = {
    2776: (2021, 2021, 2021, "First Partner Pack: the 25th-anniversary jumbo packs, May-Oct 2021"),
    2205: (2000, 2000, 2000, "Pikachu World Collection: Pokemon Park 2000, Sydney, Sept 2000"),
    2175: (None, 2008, 2009, "Burger King: Diamond & Pearl and Platinum promos, per its product names"),
    2332: (None, None, None, "Professor Program: one season per card, in its product name"),
    2289: (None, None, None, "Blister Exclusives: Cosmos Holo prints from many sets"),
}


def _get(url, as_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return json.loads(body) if as_json else body.decode()


def fetch(dest=TCGCSV_DIR):
    """Download TCGplayer's English Pokemon sets + singles and its Japanese set names into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "last-updated.txt").write_text(_get(f"{TCGCSV}/last-updated.txt", as_json=False))
    (dest / "groups_jp.json").write_text(json.dumps(_get(f"{TCGCSV}/tcgplayer/{JP}/groups")))
    groups = _get(f"{TCGCSV}/tcgplayer/{EN}/groups")
    (dest / "groups.json").write_text(json.dumps(groups))
    for g in groups["results"]:
        (dest / f"{g['groupId']}.json").write_text(json.dumps(_get(f"{TCGCSV}/tcgplayer/{EN}/{g['groupId']}/products")))
        time.sleep(0.3)
    print(f"tcgcsv: {len(groups['results'])} sets -> {dest}")


def ext(product, key):
    for e in product.get("extendedData") or []:
        if e["name"] == key:
            return e["value"]
    return None


def norm_number(s):
    """'004/102' -> '4', 'H01/H32' -> 'H1', 'SWSH286' -> 'SWSH286', '#21a' -> '21A'."""
    s = re.sub(r"[\s-]", "", (s or "").strip().lstrip("#").split("/")[0].upper())
    m = NUM.match(s)
    return f"{m.group(1)}{int(m.group(2))}{m.group(3)}" if m else s


def ascii_lower(s):
    s = (s or "").replace("♀", " f").replace("♂", " m")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def words_text(s):
    """lower-case ascii, every run of non-alphanumerics -> one space, padded with spaces."""
    return " " + re.sub(r"[^a-z0-9]+", " ", ascii_lower(s)).strip() + " "


def compact(s):
    return re.sub(r"[^a-z0-9]", "", ascii_lower(s))


REGIONAL = ("alolan", "galarian", "hisuian", "paldean")


def name_keys(names):
    """compact forms of each name, also without a leading Mega/M: alt writes "Megacameruptex" and
    "Darkrai Ex" for TCGplayer's "M Camerupt EX" and "Mega Darkrai ex". A regional prefix is
    dropped, or "Galarian Sirfetch'd" would agree with "Galarian Darumaka" on its first 6 letters."""
    keys = set()
    for n in names:
        w = words_text(n).split()
        if len(w) > 1 and w[0] in REGIONAL:
            w = w[1:]
        for form in (w, w[1:] if len(w) > 1 and w[0] in ("m", "mega") else w):
            k = "".join(form)
            keys.add(k[4:] if k.startswith("mega") and len(k) > 7 else k)
            keys.add(k)
    return {k for k in keys if k}


def alt_name(row):
    """alt's subject, every '/'-separated name ("Lunala/Solgaleo Gx"), not its '/(...)' notes;
    alt's "Mew Gold Star" is TCGplayer's "Mew Star"."""
    return name_keys(words_text(p).replace(" gold star ", " star ") for p in (row["subject"] or "").split("/")
                     if p.strip() and not p.lstrip().startswith("("))


def tcg_name(product):
    """the card name before ' - 001/100' or a bracket, every '&'-joined name ("Solgaleo & Lunala GX")."""
    return name_keys(re.split(r"\s+&\s+", re.split(r"\s+-\s+|\s*[(\[]", product["name"])[0]))


def species_of(key, species):
    """the longest species name a compact name starts with ('mewtwoex' -> 'mewtwo'), or None;
    `species` is sorted longest first"""
    return next((s for s in species if key.startswith(s)), None)


def base_species(names):
    """compact base species, longest first. The roster also lists forms and pairs ("Unown X",
    "Flying Pikachu", "Entei & Raikou"): an entry with a word that is itself a species is dropped,
    so "Unown X" still agrees with "Unown [X]" while "Mewtwo" never agrees with "Mew"."""
    names = [n for n in names if "&" not in n]
    single = {words_text(n).strip() for n in names if len(words_text(n).split()) == 1}
    keep = {compact(n) for n in names if not (len(words_text(n).split()) > 1 and set(words_text(n).split()) & single)}
    return sorted(keep, key=len, reverse=True)


SUFFIXES = ("vstar", "vmax", "break", "lvx", "star", "gx", "ex", "v")   # longest first


def suffix_of(rest):
    return next((x for x in SUFFIXES if rest.startswith(x)), None)


def names_agree(a, b, species=(), strict_suffix=False):
    """any name of one (alt's) agrees with any of the other (TCGplayer's) on their first 6 letters
    (fewer if shorter), unless they start with different species: "Mew" never agrees with "Mewtwo".
    alt's subject drops suffixes all the time ("Groudon" for Groudon Star, "Zekrom" for Zekrom EX),
    so they only count with strict_suffix, for a promo found by its bare number: alt's "Yveltal Ex
    #006" is not TCGplayer's "Yveltal - XY06"."""
    for x in a:
        for y in b:
            k = min(len(x), len(y), 6)
            if k >= 3 and x[:k] == y[:k]:
                sx, sy = species_of(x, species), species_of(y, species)
                if sx and sy and sx != sy:
                    continue
                if strict_suffix and sx and sx == sy and (sa := suffix_of(x[len(sx):])) and sa != suffix_of(y[len(sy):]):
                    continue
                return True
    return False


def qualifier_text(product):
    """'Pichu - 28/123 (Prerelease) [Staff]' -> ' prerelease staff '"""
    return words_text(" ".join(re.findall(r"[(\[]([^)\]]*)[)\]]", product["name"])))


def qualifier_words(product):
    """'Charizard (Cosmos Holo)' -> {'cosmos', 'holo'}. A bare number is TCGplayer telling two
    same-named cards apart ('Gyarados (21)'), not a variant, and alt's name always has the number."""
    return {w for w in qualifier_text(product).split() if not w.isdigit()} - QUALIFIER_STOP


def alt_text(row):
    return words_text(" ".join((row["card_name"] or "", row["variety"] or ""))).replace(" pre release ", " prerelease ")


def alt_words(row, product):
    """alt's name + variety words, minus the words of the candidate's own card name: "Team Rocket's
    Meowth" doesn't name the "(Team Rocket)" variant of itself."""
    own = set(words_text(re.split(r"\s+-\s+|\s*[(\[]", product["name"])[0]).split())
    return set(alt_text(row).split()) - QUALIFIER_STOP - own


def named_words(row, p):
    """the qualifier word set of p that alt's name spells out in full (its bracket, or what its
    set implies), or None"""
    words = alt_words(row, p)
    q = qualifier_words(p)
    if q and not p["set_qualifier"] and q <= words:
        return q
    return next((imp for imp in p["implied"] if imp <= words), None)


def names_qualifier(row, p):
    return named_words(row, p) is not None


def set_name_forms(groups):
    """Every set name as words, with and without its code ('SV08: Surging Sparks', 'XY - Steam
    Siege'): a bracket that is just a set name ('Ampharos - 40/114 (XY Steam Siege)', a theme-deck
    reprint) is where the card came from, not a variant alt's name could ask for."""
    forms = set()
    for g in groups:
        w = [x for x in words_text(g["name"]).split() if x not in ("and", "pokemon")]
        forms.add(" ".join(w))
        for k in (1, 2):
            if len(w) > k:
                forms.add(" ".join(w[k:]))
    return forms


def japanese_set_names(src, english_groups):
    """TCGplayer's Japanese set names minus their code ('SM8: Super-Burst Impact' -> 'super burst
    impact'), keeping only names that can't be mistaken for an English set."""
    english = " | ".join(words_text(g["name"]) for g in english_groups)
    out = set()
    for g in json.loads((src / "groups_jp.json").read_text())["results"]:
        # 'Pokemon Jungle' (Japanese) is the English Jungle too: compare without "pokemon"
        name = words_text(re.sub(r"^[A-Za-z0-9+]+:\s*", "", g["name"])).replace(" pokemon ", " ")
        if len(name.split()) >= 2 and len(name.strip()) >= 10 and name not in english:
            out.add(name)
    return out


def load_catalog(src=TCGCSV_DIR):
    # TCGplayer has no release date for 19 old sets (POP Series, Nintendo Promos...); the catalog
    # build stamps them with its own time, so a date within a day of the build means "undated"
    built = date.fromisoformat((src / "last-updated.txt").read_text()[:10])
    groups = {g["groupId"]: g for g in json.loads((src / "groups.json").read_text())["results"]}
    by_number, by_bare, by_name = defaultdict(list), defaultdict(list), defaultdict(list)
    for gid, g in groups.items():
        undated = abs((date.fromisoformat(g["publishedOn"][:10]) - built).days) <= 1
        g["year"] = None if undated else int(g["publishedOn"][:4])
        g["promo"] = bool(PROMO_SET.search(g["name"]))
        for p in json.loads((src / f"{gid}.json").read_text())["results"]:
            number = ext(p, "Number")
            if number:                                   # singles only; sealed product has no number
                p["group"] = g
                p["number"] = number
                p["key"] = tcg_name(p)
                num = norm_number(number)
                by_number[num].append(p)
                bare = PROMO_PREFIX.match(num)
                if bare and g["promo"]:
                    by_bare[bare.group(2)].append(p)
                by_name[compact(re.split(r"\s+-\s+|\s*[(\[]", p["name"])[0])].append(p)
    set_forms = set_name_forms(groups.values())
    for ps in by_number.values():
        for p in ps:
            q = " ".join(x for x in qualifier_text(p).split() if x != "and")
            p["set_qualifier"] = q in set_forms
            p["implied"] = next((alts for pat, alts in SET_IMPLIES if pat.search(p["group"]["name"])), ())
    return groups, (by_number, by_bare, by_name), japanese_set_names(src, groups.values())


SERIES = {"sm": "sun moon", "swsh": "sword shield", "sv": "scarlet violet", "bw": "black white",
          "hgss": "heartgold soulsilver", "dp": "diamond pearl", "me": "mega evolution", "pop": "organized play"}
SPAN_MIN = 5            # confident matches a set needs before its year span replaces its release date
SET_STOP = {"pokemon", "the", "and", "of", "tcg", "set", "promos", "promo", "cards", "card", "products",
            "holo", "reverse", "foil", "1st", "edition", "en", "english"}


def set_words(name):
    """distinctive words of a set name; series codes spelled out ("SM - Team Up" -> sun moon team up)"""
    out = set()
    for w in words_text(name).split():
        code = re.fullmatch(r"(sv|swsh|sm|me)\d+[a-z]?", w)
        out |= set(SERIES.get(code.group(1) if code else w, w).split())
    return out - SET_STOP


def year_fits(row, group, spans=None):
    """alt's year against the years the set's confidently matched cards carry (+/- 1), else its
    release year: +/- 1, a promo set released no later than a year after the card, an undated set
    anything."""
    try:
        year = int(row["year"])
    except (TypeError, ValueError):
        return False
    span = (spans or {}).get(group["groupId"])
    if span:
        return span[0] - 1 <= year <= span[1] + 1
    if group["year"] is None:
        return True
    if group["promo"]:
        return group["year"] <= year + 1
    return abs(group["year"] - year) <= 1


def named_variant(row, cands):
    """The candidate (any set) whose bracketed qualifier alt's name spells out in full
    ("Prerelease", "Poke Ball Pattern", "Unova Poster Collection"); the most specific wins,
    a tie is None. No year check: catch-all sets ("Nintendo Promos") hold prerelease cards from
    years after the Black Star promos that date them."""
    hits = sorted(((len(q), p) for p in cands if (q := named_words(row, p)) and q - GENERIC), key=lambda t: -t[0])
    if hits and (len(hits) == 1 or hits[0][0] > hits[1][0]):
        return hits[0][1]
    return None


def pick_variant(row, cands):
    """Several products with one number in one set, none named in full: the one sharing the most
    distinctive qualifier words with alt's name ("Energy Reverse" -> "(Energy Symbol Pattern)",
    not "(Friend Ball)"), else the plain card."""
    full = sorted(((len(q), p) for p in cands if (q := named_words(row, p))), key=lambda t: -t[0])
    if full and (len(full) == 1 or full[0][0] > full[1][0]):
        return full[0][1], "variant"                      # e.g. "(Reverse Holo)" when alt says so
    scored = sorted(((len((qualifier_words(p) - GENERIC) & alt_words(row, p)), p) for p in cands), key=lambda t: -t[0])
    if scored[0][0] > 0:
        if len(scored) == 1 or scored[0][0] > scored[1][0]:
            return scored[0][1], "variant"
        return None, "ambiguous_variant"
    plain = [p for p in cands if not qualifier_words(p)]
    if len(plain) == 1:
        return plain[0], "plain_variant"
    return None, "ambiguous_variant"


def result(row, p, how):
    return {"asset_id": row["asset_id"], "tcgplayer_id": p["productId"], "image_url": IMAGE_URL.format(p["productId"]),
            "tcgplayer_name": p["name"], "tcgplayer_set": p["group"]["name"], "tcgplayer_number": p["number"],
            "tcgplayer_rarity": ext(p, "Rarity") or "", "match": how}


def build(feed=FEED, src=TCGCSV_DIR, out=OUT, report=None, characters=CHARACTERS, catalog=None, sets=None):
    groups, (by_number, by_bare, by_name), japanese = load_catalog(src)
    rows = [r for r in csv.DictReader(open(feed, newline="", encoding="utf-8")) if english_tcg(r)]
    species = base_species(r["character"] for r in csv.DictReader(open(characters, newline="", encoding="utf-8")))

    def skip(r):
        # set + variety only: "Holon Research Tower" is a Japanese set and an English card
        text = words_text(" ".join((r["set"] or "", r["variety"] or "")))
        return bool(NOT_ON_TCGPLAYER.search(text)) or any(name in text for name in japanese)

    def fits_size(r, p):
        # TCGplayer's oversized "Jumbo Cards" reuse the promo's number ("Eevee GX - SM175 (SM Black
        # Star Promo)"); alt.xyz names them jumbo / oversize when that's what was graded
        return p["group"]["name"] != "Jumbo Cards" or bool(re.search(r" (jumbo|oversized?) ", alt_text(r)))

    cands = {}
    for r in rows:
        name = alt_name(r)
        if skip(r):
            cands[r["asset_id"]] = []
        elif (r["card_number"] or "").strip():
            num = norm_number(r["card_number"])
            cands[r["asset_id"]] = [p for p in by_number.get(num, []) if names_agree(name, p["key"], species) and fits_size(r, p)] or \
                                   [p for p in by_bare.get(num, []) if names_agree(name, p["key"], species, True) and fits_size(r, p)]
        else:   # no number on alt.xyz ("2000 Pokemon Promo Movie 2000 Ancient Mew"): the exact name,
            # and only a name TCGplayer has at most 3 products for ("Arcanine" could be any of 100)
            same = by_name.get(compact((r["subject"] or "").split("/")[0]), [])
            cands[r["asset_id"]] = [p for p in same if fits_size(r, p)] if len(same) <= 3 else []

    # set alignment: each alt set votes for the TCGplayer sets its candidates are in
    def keys(r):
        return (("full", r["year"], r["set"], r["variety"]), ("set", r["year"], r["set"]))
    votes, voters = defaultdict(Counter), Counter()
    for r in rows:
        gids = {p["group"]["groupId"] for p in cands[r["asset_id"]]}
        for k in keys(r):
            if gids:
                voters[k] += 1
            for gid in gids:
                votes[k][gid] += 1 / len(gids)

    def aligned(r, gid):
        """None: alt's set is too small to vote; else whether it voted enough for this set."""
        levels = [(votes[k][gid], voters[k]) for k in keys(r) if voters[k] >= MIN_VOTERS]
        if not levels:
            return None, 0
        share = max(v / n for v, n in levels)
        ok = any(v / n >= MIN_SHARE or (v / n >= MIN_SHARE_VOTES[0] and v >= MIN_SHARE_VOTES[1]) for v, n in levels)
        return ok, share

    def decide(r, spans):
        """(product, how) for one row, or (None, why not)"""
        cs = cands[r["asset_id"]]
        if not cs:
            return None, "not_on_tcgplayer" if skip(r) else "no_candidate", False
        p = named_variant(r, cs)
        if p is not None:
            return p, "named_variant", False
        if VARIANT_WORDS.search(NOT_A_VARIANT.sub(" ", alt_text(r))):
            return None, "variant_not_found", False
        kept, voted = [], set()
        for p in cs:
            if not year_fits(r, p["group"], spans):
                continue
            ok, share = aligned(r, p["group"]["groupId"])
            if ok is None and p["group"]["year"] is None and p["group"]["groupId"] not in (spans or {}) \
                    and (r["card_number"] or "").strip():
                continue        # an undated set with no span accepts any year: a number match there
                                # needs alt's vote (an exact, near-unique name is evidence enough)
            if ok is not False:
                kept.append((share, p))
                if ok:
                    voted.add(p["group"]["groupId"])
        if not kept:
            return None, "set_not_aligned", False
        best = defaultdict(float)
        for s, p in kept:
            best[p["group"]["groupId"]] = max(best[p["group"]["groupId"]], s)
        how = "unique"
        if len(best) > 1:
            ranked = sorted(best.items(), key=lambda t: -t[1])
            shadowless = [gid for gid in best if "Shadowless" in groups[gid]["name"]]
            # alt's '/(...)' notes sometimes name the product: "Gardevoir/(Ex Battle Stadium)"
            notes = set_words(" ".join(re.findall(r"\(([^)]*)\)", r["subject"] or "")))
            in_notes = [g for g in best if (w := set_words(groups[g]["name"])) and w <= notes]
            if len(in_notes) == 1:
                gid = in_notes[0]
            elif ranked[0][1] > 0 and ranked[0][1] >= 2 * ranked[1][1]:
                gid = ranked[0][0]
            elif len(best) == 2 and len(shadowless) == 1 and r["set"] == "Pokemon Base Set":
                # alt's one Base Set is TCGplayer's two; its 1st Edition and Shadowless print
                # runs are in "Base Set (Shadowless)"
                first = re.search(r" (1st edition|shadowless) ", alt_text(r))
                gid = shadowless[0] if first else next(g for g in best if g != shadowless[0])
            else:
                # last resort, mostly for alt sets too small to vote: the set whose name shares
                # the most words with alt's set + variety ("Phantasmal Flames - Pfl En - English")
                overlap = sorted(((len(set_words(groups[g]["name"]) & set_words(r["set"] + " " + r["variety"])), g)
                                  for g in best), reverse=True)
                if overlap[0][0] == 0 or overlap[0][0] == overlap[1][0]:
                    return None, "ambiguous_set", False
                gid = overlap[0][1]
            kept = [(s, p) for s, p in kept if p["group"]["groupId"] == gid]
            how = "best_set"
        products = [p for _, p in kept]
        if len(products) > 1:
            p, how = pick_variant(r, products)
        else:
            p = products[0]
        # only a set alt's own set voted for dates it: a lone Celebrations reprint matched to
        # POP Series 5 must not stretch POP Series 5 to 2021
        return p, how, p is not None and p["group"]["groupId"] in voted

    # Pass 1 dates each TCGplayer set by the alt.xyz cards it confidently matches; pass 2 matches
    # again against those spans. A promo set's release date is only where it starts (WoTC Promo:
    # 1999-2003, not "1999 or later"), and TCGplayer has no date for some old sets (POP Series 5:
    # 2007), so a 2022 Battle Academy Pikachu and a 2021 Celebrations reprint stay out of both.
    years = defaultdict(list)
    for r in rows:
        p, how, voted = decide(r, None)
        if voted and str(r["year"]).isdigit():
            years[p["group"]["groupId"]].append(int(r["year"]))
    spans = {gid: (min(ys), max(ys)) for gid, ys in years.items() if len(ys) >= SPAN_MIN}

    out_rows, reasons, decided = [], Counter(), {}
    for r in rows:
        p, how, _ = decide(r, spans)
        decided[r["asset_id"]] = (p, how)
        if p is None:
            reasons[how] += 1
            continue
        reasons["matched"] += 1
        out_rows.append(result(r, p, how))

    out_rows.sort(key=lambda d: d["asset_id"])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(out_rows)
    print(f"tcgplayer_ids: {len(out_rows)} of {len(rows)} English cards -> {out}")
    for k, v in reasons.most_common():
        print(f"  {k:<18} {v:>6}")
    if report:
        matched = {d["asset_id"] for d in out_rows}
        with open(report, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["asset_id", "card_name", "variety", "card_number", "reason", "candidates"])
            for r in rows:
                if r["asset_id"] not in matched:
                    cs = cands[r["asset_id"]]
                    w.writerow([r["asset_id"], r["card_name"], r["variety"], r["card_number"],
                                ("not_on_tcgplayer" if skip(r) else "no_candidate") if not cs else "refused",
                                " | ".join(f"{p['productId']}:{p['group']['name']}:{p['name']}" for p in cs[:6])])
    if catalog or sets:
        set_rows = set_years(groups, rows, decided)
        if sets:
            write_csv(sets, SETS_COLS, set_rows)
            print(f"tcgplayer_sets: {len(set_rows)} sets -> {sets}  "
                  f"({dict(Counter(s['year_source'] for s in set_rows))})")
        if catalog:
            def voted_set(r):
                best = max(((votes[k][gid] / voters[k], gid) for k in keys(r) if voters[k] >= MIN_VOTERS
                            for gid in votes[k]), default=(0, None))
                return groups[best[1]]["name"] if best[0] >= MIN_SHARE else ""
            year_of = {s["group_id"]: s["year"] for s in set_rows}
            cat_rows = catalog_rows(rows, decided, cands, by_number, skip, voted_set, year_of)
            write_csv(catalog, CATALOG_COLS, cat_rows)
            print(f"card_catalog: {len(cat_rows)} cards -> {catalog}  "
                  f"({dict(Counter(c['status'] for c in cat_rows))})")
    return out_rows


def write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def print_run(row):
    t = alt_text(row) + words_text(row.get("set") or "")
    return next((label for word, label in ((" 1st edition ", "1st Edition"), (" shadowless ", "Shadowless"),
                                           (" reverse ", "Reverse Holo")) if word in t), "")


def not_english(row):
    """an unmatched row that alt.xyz files as English but isn't an English TCG card"""
    number = re.sub(r"[\s#/]", "", (row.get("card_number") or "").upper())
    return bool(NOT_ENGLISH.search(alt_text(row)) or TOPPS_MOVIE.search(words_text(row.get("card_name") or ""))
                or JP_PROMO_NUMBER.match(number))


def set_years(groups, rows, decided):
    """one row per TCGplayer set: TCGplayer's release year, else one learned from the alt.xyz
    cards matched to it, else MANUAL_SET_YEARS, else multi_year (no single year)."""
    linked = defaultdict(list)
    for r in rows:
        p, _ = decided[r["asset_id"]]
        if p is not None and str(r.get("year") or "").isdigit():
            linked[p["group"]["groupId"]].append(int(r["year"]))
    out = []
    for gid, g in sorted(groups.items()):
        ys = linked.get(gid, [])
        span = (min(ys), max(ys)) if ys else (None, None)
        year, source, note = g["year"], "published", ""
        if year is None:
            top, n = Counter(ys).most_common(1)[0] if ys else (None, 0)
            if len(ys) >= SPAN_MIN and n >= LEARNED_SHARE * len(ys):
                year, source = top, "learned"
            elif gid in MANUAL_SET_YEARS:
                year, span_from, span_to, note = MANUAL_SET_YEARS[gid]
                source = "manual" if year else "multi_year"
                span = (span[0] or span_from, span[1] or span_to)
            else:
                source = "multi_year"
        out.append({"group_id": gid, "name": g["name"], "published": "" if g["year"] is None else g["publishedOn"][:10],
                    "year": year or "", "year_source": source, "span_from": span[0] or "", "span_to": span[1] or "",
                    "linked_cards": len(ys), "note": note})
    return out


def catalog_rows(rows, decided, cands, by_number, skip, voted_set, year_of):
    out, matched, maybe = [], set(), set()
    for r in rows:
        p, how = decided[r["asset_id"]]
        base = {"asset_id": r["asset_id"], "year": r.get("year") or "", "print_run": print_run(r)}
        if p is not None:
            matched.add(p["productId"])
            out.append({**base, "tcgplayer_id": p["productId"], "status": "linked", "how": how, "name": p["name"],
                        "set": p["group"]["name"], "set_source": "tcgplayer", "number": p["number"],
                        "rarity": ext(p, "Rarity") or ""})
            continue
        cs = cands[r["asset_id"]]
        if skip(r) or not_english(r):
            status, how = "not_english", "not_on_tcgplayer" if skip(r) else "not_english_tcg"
        else:
            status = "alt_only"
            maybe.update(c["productId"] for c in cs)
        in_sets = {c["group"]["name"] for c in cs}
        guess, source = (in_sets.pop(), "candidates") if len(in_sets) == 1 else (voted_set(r), "voted")
        out.append({**base, "tcgplayer_id": "", "status": status, "how": how, "name": r.get("subject") or "",
                    "set": guess, "set_source": source if guess else "", "number": r.get("card_number") or "",
                    "rarity": ""})
    for ps in by_number.values():
        for p in ps:
            if p["productId"] in matched:
                continue
            out.append({"asset_id": "", "tcgplayer_id": p["productId"], "status": "tcgplayer_only",
                        "how": "candidate_of_alt_row" if p["productId"] in maybe else "", "name": p["name"],
                        "set": p["group"]["name"], "set_source": "tcgplayer", "number": p["number"],
                        "rarity": ext(p, "Rarity") or "", "year": year_of.get(p["group"]["groupId"], ""),
                        "print_run": ""})
    out.sort(key=lambda c: (c["asset_id"] == "", c["asset_id"], int(c["tcgplayer_id"] or 0)))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--tcgcsv", type=Path, default=TCGCSV_DIR)
    ap.add_argument("--feed", type=Path, default=FEED)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--catalog", type=Path, default=CATALOG)
    ap.add_argument("--sets", type=Path, default=SETS)
    a = ap.parse_args()
    if a.fetch:
        fetch(a.tcgcsv)
    build(a.feed, a.tcgcsv, a.out, a.report, catalog=a.catalog, sets=a.sets)
