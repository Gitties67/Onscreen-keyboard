#!/usr/bin/env python3
"""Build the country-flag pictures and print the flag entries for emojis.py.

Pictures: Noto Emoji's waved flags (third_party/region-flags/waved-svg,
public domain), rendered to 72px PNGs in emoji_png/ under the same names as
the other emoji.  Names: the iso-codes package (/usr/share/iso-codes, on Mint
by default).  Needs network and GdkPixbuf with its SVG loader (librsvg).

    python3 tools/build_flags.py > /tmp/flags.txt
    # then paste the printed lines into the Flags section of emojis.py
"""
import json
import os
import sys
import urllib.request

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "emoji_png")
SVG_URL = ("https://raw.githubusercontent.com/googlefonts/noto-emoji/main/"
           "third_party/region-flags/waved-svg/")
ISO = "/usr/share/iso-codes/json/iso_3166-1.json"
SIZE = 72

# Words people type for a country beyond its name (people, language, nicknames)
EXTRA = {
    "AE": ["uae", "emirates", "emirati", "dubai"], "AF": ["afghan"],
    "AR": ["argentinian", "argentine"], "AT": ["austrian"],
    "AU": ["aussie", "australian", "oz"], "BA": ["bosnian"],
    "BD": ["bangladeshi"], "BE": ["belgian"], "BG": ["bulgarian"],
    "BR": ["brazilian"], "CA": ["canadian"], "CH": ["swiss"],
    "CL": ["chilean"], "CN": ["chinese"], "CO": ["colombian"],
    "CD": ["drc", "dr congo"], "CR": ["costa rica", "costarica"], "CU": ["cuban"], "CZ": ["czech", "czechia"],
    "DE": ["german", "deutschland"], "DK": ["danish", "dane"],
    "DO": ["dominican republic"], "EG": ["egyptian"], "ES": ["spanish", "espana"],
    "ET": ["ethiopian"], "FI": ["finnish", "finn"], "FJ": ["fijian"],
    "FR": ["french"], "GB": ["uk", "britain", "british", "great britain"],
    "GH": ["ghanaian"], "GR": ["greek"], "HK": ["hong kong", "hongkong"],
    "HR": ["croatian"], "HU": ["hungarian"], "ID": ["indonesian"],
    "IE": ["irish", "eire"], "IL": ["israeli"], "IN": ["indian"],
    "IQ": ["iraqi"], "IR": ["iranian", "persian"], "IS": ["icelandic"],
    "IT": ["italian"], "JM": ["jamaican"], "JP": ["japanese"], "KE": ["kenyan"],
    "KH": ["cambodian"], "KP": ["north korea"], "KR": ["korea", "korean", "south korea"],
    "LB": ["lebanese"], "LK": ["sri lanka", "lanka", "srilanka"],
    "LT": ["lithuanian"], "LV": ["latvian"], "MA": ["moroccan"],
    "MM": ["burma", "burmese"], "MT": ["maltese"], "MX": ["mexican"],
    "MY": ["malaysian"], "NG": ["nigerian"], "NL": ["dutch", "holland"],
    "NO": ["norwegian"], "NP": ["nepali", "nepalese"],
    "NZ": ["new zealand", "zealand", "nz", "kiwi"], "PE": ["peruvian"],
    "PG": ["papua new guinea", "png"], "PH": ["filipino", "pinoy", "philippine"],
    "PK": ["pakistani"], "PL": ["polish"], "PR": ["puerto rico", "puerto rican"],
    "PS": ["palestinian"], "PT": ["portuguese"], "QA": ["qatari"],
    "RO": ["romanian"], "RS": ["serbian"], "RU": ["russian"],
    "SA": ["saudi", "saudi arabia"], "SE": ["swedish", "swede"],
    "SG": ["singaporean"], "SI": ["slovenian"], "SK": ["slovak"],
    "SV": ["el salvador", "salvadoran"], "SY": ["syrian"], "TH": ["thai"],
    "TR": ["turkey", "turkish", "turkiye"], "TW": ["taiwanese"], "UA": ["ukrainian"],
    "US": ["usa", "america", "american"], "UY": ["uruguayan"],
    "VE": ["venezuelan"], "VN": ["vietnamese"], "ZA": ["south africa", "south african"],
    "ZW": ["zimbabwean"],
}
# Name words that aren't a country on their own ("south", "islands")
STOP = {"and", "of", "the", "south", "north", "east", "west", "united", "states",
        "republic", "new", "islands", "island", "saint", "st", "kingdom", "arab",
        "central", "african", "equatorial", "democratic", "people's", "federated",
        "sri", "costa", "el", "san", "sierra", "burkina", "cape", "dominican",
        "marshall", "solomon", "cook", "virgin", "british", "french", "american",
        "heard", "mcdonald", "territories", "territory", "outlying", "minor",
        "sao", "principe", "tome", "city", "holy", "see", "papua", "guinea",
        "korea", "sudan", "verde", "lucia", "kitts", "nevis", "vincent",
        "grenadines", "barthélemy", "martin", "pierre", "miquelon", "helena",
        "ascension", "tristan", "da", "cunha", "us", "u.s.", "puerto", "rico",
        "hong", "kong", "faroe", "cayman", "turks", "caicos", "northern",
        "mariana", "christmas", "cocos", "keeling", "norfolk", "georgia",
        "sandwich", "bouvet", "jan", "mayen", "svalbard", "falkland", "malvinas",
        "wallis", "futuna", "trinidad", "tobago", "antigua", "barbuda", "bosnia",
        "herzegovina", "timor", "leste", "viet", "nam", "macao", "polynesia",
        "southern", "antarctic", "lands", "isle", "man", "bonaire", "sint",
        "eustatius", "saba", "maarten", "caledonia", "great", "britain", "ireland", "indian",
        "ocean", "dutch", "part"}
SUBDIVISIONS = [   # tag sequences: 🏴 + gb-eng / gb-sct / gb-wls
    ("gbeng", "england", ["england", "english"]),
    ("gbsct", "scotland", ["scotland", "scottish", "scot"]),
    ("gbwls", "wales", ["wales", "welsh", "cymru"]),
]


def _fetch_png(name: str) -> bool:
    out = os.path.join(OUT, name + ".png")
    if os.path.exists(out):
        return True
    try:
        svg = urllib.request.urlopen(SVG_URL + name + ".svg", timeout=30).read()
        if not svg.lstrip().startswith(b"<"):   # a git symlink: the file it points to
            svg = urllib.request.urlopen(SVG_URL + svg.decode().strip(), timeout=30).read()
    except Exception as exc:
        print(f"[flags] {name}: {exc}", file=sys.stderr)
        return False
    loader = GdkPixbuf.PixbufLoader.new_with_mime_type("image/svg+xml")
    loader.set_size(SIZE, SIZE)
    loader.write(svg)
    loader.close()
    loader.get_pixbuf().savev(out, "png", [], [])
    return True


NAMES = {"CD": "DR Congo",   # iso-codes calls both Congos "Congo, ..."
         "VG": "British Virgin Islands", "VI": "US Virgin Islands"}


def _name(entry: dict) -> str:
    if entry["alpha_2"] in NAMES:
        return NAMES[entry["alpha_2"]]
    name = entry.get("common_name", entry["name"])
    if ", " in name:                       # "Korea, Democratic People's Republic of"
        name = name.split(", ")[0]
    return name


def main():
    countries = json.load(open(ISO, encoding="utf-8"))["3166-1"]
    entries = []
    word_count: dict[str, int] = {}
    for c in countries:
        words = [w for w in _name(c).lower().replace("(", "").replace(")", "").split()
                 if w not in STOP and len(w) > 2]
        for w in words:
            word_count[w] = word_count.get(w, 0) + 1
    for c in sorted(countries, key=_name):
        code, flag = c["alpha_2"], c["flag"]
        png = "emoji_u" + "_".join(f"{ord(ch):x}" for ch in flag)
        if not _fetch_png(png):
            continue
        name = _name(c)
        words = [w for w in name.lower().replace("(", "").replace(")", "").split()
                 if w not in STOP and len(w) > 2 and word_count[w] == 1]
        kws = list(dict.fromkeys([name.lower()] + words + EXTRA.get(code, [])))
        entries.append((flag, f"{name.lower()} flag", kws))
    for tag, name, kws in SUBDIVISIONS:
        flag = "🏴" + "".join(chr(0xE0000 + ord(ch)) for ch in tag) + "\U000E007F"
        png = "emoji_u" + "_".join(f"{ord(ch):x}" for ch in flag)
        if _fetch_png(png):
            entries.append((flag, f"{name} flag", kws))
    for flag, name, kws in entries:
        print(f'    ("{flag}", ' + f'"{name}",'.ljust(29) + " ["
              + ", ".join(f'"{k}"' for k in kws) + "]),")
    print(f"[flags] {len(entries)} flags", file=sys.stderr)


if __name__ == "__main__":
    main()
