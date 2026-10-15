#!/usr/bin/env python3
"""Fanatics Collect Buy Now listing -> alt.xyz asset_id (2026-10-15).

Fanatics' search index gives a listing's title ("2009 Pokemon Diamond & Pearl Black Star
Promo Holo Arceus Lv.X #DP53 PSA 9 MINT"), year, card number, grade and grader, but no
structured set name. alt.xyz's card names come from the same place (the PSA label), so
the join is by words, in the tcgplayer_ids.py spirit: never guess, refuse anything unclear,
and say why in the log so the refusals can be read.

Per listing, against history/assets.csv (every card alt.xyz has a PSA pop for):
  candidates  the alt.xyz cards with the listing's year and card number (letters kept,
              leading zeros and "/total" dropped, tcgplayer_ids.norm_number); a listing
              without a number (not in the record, not "#..." in the title) matches nothing.
  required    every word of the card's subject and variety (print run) must be in the title
              ("Arceus Lv X" = "Arceus Lv.X" = "Arceus Level X" = "arceus lvx"; "Gengar/Mimikyu Gx"
              both names; "Reverse Foil" = "Reverse Holo"), except rarity and finish words
              (OPTIONAL: Holo, Promo, Special Illustration Rare / SIR, ...) which Fanatics abbreviates
              or leaves out; those only count towards the winner below.
  language    the title's language word (Japanese, German, ...) must be the card's and vice
              versa; no word on either side = English. Fanatics' category decides when the
              title says nothing ("Pokémon (Japanese)" -> a Japanese card only).
  variants    a distinguishing word (DISTINCT: Shadowless, 1st Edition, Reverse, Error, Staff,
              Prerelease, ...) in the title but not in the card's name disqualifies the card: the
              listing is some other print of it, which may or may not be in alt.xyz. One anywhere
              in the card's name (alt.xyz puts "Reverse Foil" in some set names) is required in the
              title.
  winner      the surviving card whose words explain a strict superset of what every other
              survivor explains (set words included); a lone survivor wins even when the
              title doesn't spell out its whole set name (alt.xyz's "Black Star Promo Arceus
              Collector's Tin" vs Fanatics' "Black Star Promo"). Two survivors that explain
              the same words, or each explain something the other doesn't, are an
              `ambiguous` refusal: alt.xyz holds near-duplicate cards (one with a PSA label
              note, one without) and the title cannot tell them apart.

Reasons written next to each listing (fanatics_listings.csv `match`):
  matched | no_number | no_candidate (nothing with that year + number) | no_survivor (the
  candidates all fail a required word, the language or a variant word) | ambiguous
"""
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tcgplayer_ids import norm_number   # noqa: E402

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets.csv"

STOP = {"and", "the", "of", "a", "an", "pokemon", "tcg", "english", "card", "cards", "psa", "mint", "gem", "mt", "nm", "em"}
LANGUAGES = {"japanese", "german", "italian", "french", "spanish", "korean", "chinese", "dutch", "portuguese",
             "cantonese", "thai", "indonesian", "russian", "polish", "brazilian", "mandarin", "traditional", "simplified",
             "taiwanese", "vietnamese", "arabic", "greek", "hebrew", "finnish", "swedish", "norwegian", "danish"}
# A word that tells one print of a card from another: in the title and not in the card's name means
# the listing is some other print (alt.xyz lists prints as separate cards, so the right one, if it has
# it, is another candidate).
DISTINCT = {"shadowless", "1st", "reverse", "error", "prerelease", "staff", "stamp", "stamped", "winner",
            "jumbo", "unlimited", "4th", "misprint", "miscut", "cosmos", "league", "championship", "championships",
            "sample", "crystal", "crosshatch", "pokeball", "masterball", "pattern", "signed", "auto", "autograph",
            "autographed"}
# Words in a card's name the title need not repeat: rarity labels and finish, which Fanatics abbreviates
# ("SIR" for alt's "Special Illustration Rare"), spells differently ("Reverse Foil" / "Reverse Holo", both
# sides) or leaves out ("Holo", "Promo" on a third-party seller's title). They still count towards which
# candidate explains the title best; a card differing from another only by them is told apart by the
# dominance rule, or refused.
OPTIONAL = {"holo", "promo", "illustration", "rare", "art", "special", "ultra", "hyper", "secret", "rainbow", "full",
            "double", "character", "gallery", "alt", "alternate", "amazing", "radiant", "trainer", "foil", "rev"}
NUMBER_IN_TITLE = re.compile(r"#\s*([A-Za-z0-9][A-Za-z0-9/.\-]*)")
CATEGORY_LANG = {"Trading Card Games > Pokémon (English)": "english", "Trading Card Games > Pokémon (Japanese)": "japanese",
                 "Trading Card Games > Pokémon (Other Languages)": "other"}


def words(text):
    """Lower-case ascii word set: '&' -> and, possessives and dots dropped ("Lv.X" -> lvx, "Collector's"
    -> collector), 'lv x' joined to lvx, stop words out."""
    s = (text or "").replace("&", " and ").replace("\u2019", "'").replace("\u2018", "'")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"'s\b", "", s).replace("'", "").replace(".", "")
    toks = re.sub(r"[^a-z0-9]+", " ", s).split()
    out = []
    for t in toks:
        if t == "x" and out and out[-1] == "lv":
            out[-1] = "lvx"
        elif t in ("1st", "first"):
            out.append("1st")
        elif t == "ed":
            out.append("edition")
        elif t == "x" and out and out[-1] == "level":
            out[-1] = "lvx"
        elif t in ("promos",):
            out.append("promo")
        elif t == "foil":
            out.append("holo")
        elif t == "etb":
            out += ["elite", "trainer", "box"]
        else:
            out.append(t)
    return {t for t in out if t not in STOP}


def subject_words(subject):
    """alt's subject, every '/'-separated name, not its '/(...)' notes (tcgplayer_ids.alt_name)."""
    out = set()
    for part in (subject or "").split("/"):
        if part.strip() and not part.lstrip().startswith("("):
            out |= words(part)
    return out


def language_of(wordset):
    return wordset & LANGUAGES


def load_assets(path=ASSETS, rows=None):
    """{(year, norm_number): [card dicts with precomputed word sets]} from history/assets.csv."""
    if rows is None:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    index = defaultdict(list)
    for r in rows:
        num = norm_number(r.get("card_number"))
        if not num or not r.get("year"):
            continue
        subj, var, sset = subject_words(r.get("subject")), words(r.get("variety")), words(r.get("set"))
        index[(r["year"].strip(), num)].append({
            "asset_id": r["asset_id"], "card_name": r.get("card_name", ""),
            "required": ((subj | var) - LANGUAGES - OPTIONAL) | ((subj | var | sset) & DISTINCT), "all": subj | var | sset,
            "language": language_of(subj | var | sset)})
    return index


def hit_number(hit):
    num = norm_number(str(hit.get("cardNumber") or ""))
    if num:
        return num
    m = NUMBER_IN_TITLE.search(hit.get("title") or "")
    return norm_number(m.group(1)) if m else ""


def hit_language(hit, title_words):
    """The language the listing is in: the title's word, else Fanatics' category when it names one."""
    lang = language_of(title_words)
    if lang:
        return lang
    cats = {CATEGORY_LANG[c] for c in (hit.get("subCategory1") or []) if c in CATEGORY_LANG}
    if cats == {"japanese"}:
        return {"japanese"}
    if cats == {"english"}:
        return set()
    return None   # "other languages" without a word, or several categories: unknown, so no language filter
                  # (candidates in different languages then explain the title equally -> ambiguous)


def match(hit, index):
    """-> (asset_id or "", reason, [candidate card names])."""
    title = hit.get("title") or ""
    year = str(hit.get("year") or "").strip()
    if not year:
        m = re.match(r"\s*((?:19|20)\d\d)\b", title)
        year = m.group(1) if m else ""
    num = hit_number(hit)
    if not num:
        return "", "no_number", []
    cands = index.get((year, num), [])
    if not cands:
        return "", "no_candidate", []
    tw = words(title) - {year}
    lang = hit_language(hit, tw)
    survivors = []
    for c in cands:
        if not c["required"] <= tw:
            continue
        if lang is not None and c["language"] != lang:
            continue
        if (tw & DISTINCT) - c["all"]:
            continue
        survivors.append(c)
    names = [c["card_name"] for c in cands]
    if not survivors:
        return "", "no_survivor", names
    if len(survivors) == 1:
        return survivors[0]["asset_id"], "matched", names
    explained = [(c, c["all"] & tw) for c in survivors]
    best = max(explained, key=lambda ce: len(ce[1]))
    if all(ce is best or ce[1] < best[1] for ce in explained):
        return best[0]["asset_id"], "matched", names
    return "", "ambiguous", [c["card_name"] for c in survivors]


def match_all(hits, index):
    """{listingUuid: (asset_id, reason, candidates)} for a list of index hits."""
    return {h["listingUuid"]: match(h, index) for h in hits if h.get("listingUuid")}


if __name__ == "__main__":
    import json
    index = load_assets()
    hits = json.load(open(sys.argv[1]))
    hits = hits.get("results", [hits])[0].get("hits", hits) if isinstance(hits, dict) else hits
    from collections import Counter
    out = match_all(hits, index)
    print(Counter(v[1] for v in out.values()))
    for h in hits:
        aid, why, cands = out.get(h["listingUuid"], ("", "", []))
        if why != "matched" or "-v" in sys.argv:
            print(f"{why:12} {h.get('year')} #{h.get('cardNumber')} | {h.get('title')} | {aid} | {cands[:4]}")
