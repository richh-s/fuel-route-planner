"""Place-name normalization so free-text city names match Census place names.

Real-world data spells the same place many ways ("Saint Johns" / "St. Johns",
"Mc Calla" / "McCalla", "Winston Salem" / "Winston-Salem"). We reduce every
name to a lookup key: lowercase, common abbreviations expanded, punctuation
and spaces removed.
"""

import re

_WORD_ALIASES = {
    "st": "saint",
    "ste": "sainte",
    "mt": "mount",
    "ft": "fort",
    "pt": "port",
}

# Legal/statistical area descriptions the Census appends to names,
# longest first so "charter township" wins over "township".
_CENSUS_SUFFIXES = (
    "consolidated government",
    "metropolitan government",
    "unified government",
    "charter township",
    "metro government",
    "city and borough",
    "urban county",
    "municipality",
    "zona urbana",
    "corporation",
    "plantation",
    "comunidad",
    "township",
    "location",
    "purchase",
    "village",
    "borough",
    "grant",
    "city",
    "town",
    "gore",
    "cdp",
)

# Some official names carry the descriptor in front, e.g. "Town of Pecos city".
_CENSUS_PREFIXES = ("town of ", "city of ", "village of ", "borough of ")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_PARENTHETICAL = re.compile(r"\s*\(.*?\)\s*")


def place_key(name: str) -> str:
    """Normalize a place name into a compact lookup key."""
    words = _NON_ALNUM.sub(" ", name.lower()).split()
    return "".join(_WORD_ALIASES.get(word, word) for word in words)


def census_suffix(raw_name: str) -> str | None:
    """Return the Census descriptor at the end of a name ('Edison township' -> 'township'), if any."""
    lowered = _PARENTHETICAL.sub(" ", raw_name).strip().lower()
    return next((suffix for suffix in _CENSUS_SUFFIXES if lowered.endswith(" " + suffix)), None)


def clean_census_name(raw_name: str, has_descriptor: bool = True) -> str:
    """Strip Census descriptors.

    'Nashville-Davidson metropolitan government (balance)' -> 'Nashville-Davidson'
    'Town of Pecos city' -> 'Pecos'

    `has_descriptor=False` is for names whose last word is part of the name
    itself (Census LSAD code "00"), e.g. 'Carson City'.
    """
    name = _PARENTHETICAL.sub(" ", raw_name).strip()
    lowered = name.lower()
    for prefix in _CENSUS_PREFIXES:
        if lowered.startswith(prefix):
            name, lowered = name[len(prefix) :], lowered[len(prefix) :]
            break
    if has_descriptor:
        suffix = census_suffix(name)
        if suffix:
            name = name[: -len(suffix)].strip()
    return name


def census_name_aliases(name: str) -> list[str]:
    """Other names a (cleaned) Census place is commonly known by.

    'Louisville/Jefferson County' -> 'Louisville'; 'Boise City' -> 'Boise'.
    """
    aliases = []
    for separator in ("/", "-"):
        if separator in name:
            aliases.append(name.split(separator)[0].strip())
    if name.lower().endswith(" city"):
        aliases.append(name[: -len(" city")].strip())
    return [alias for alias in dict.fromkeys(aliases) if alias and alias != name]
