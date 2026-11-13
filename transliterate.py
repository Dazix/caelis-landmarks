"""
A Latin spelling of a peak name written in another script, for readers who cannot read that script.

generate_peaks.py asks this only when OpenStreetMap carries no Latin spelling of its own (`name:en`,
`int_name`, `name:*-Latn`, ...). The character tables are `anyascii` (pinned in
`requirements-peaks.txt`); what it gets wrong for place names is corrected here:

- Cyrillic: the soft sign's apostrophe is dropped, as maps and signs drop it; a Bulgarian `ъ` before
  a consonant is a vowel ("бърдо" -> "Bardo"), and Bulgarian `щ` is "sht"; Ukrainian letters
  follow the Ukrainian national system ("г" -> "h", "и" -> "y").
- Greek: the digraphs of ELOT 743 ("ου" -> "ou", "αυ" -> "av", "ευ" -> "ev"), not "oy".
- Korean: the Revised Romanization as it is pronounced, with final consonants ("죽바위산" ->
  "Jukbawisan", "한라산" -> "Hallasan"), not letter by letter.
- Chinese characters: pinyin without tones, one word with the generic last character ("山", "峰",
  "岭" ...) apart, as maps write them ("孙家山" -> "Sunjia Shan").
- Japanese: kana by Hepburn without long-vowel marks ("きちじょうざん" -> "Kichijozan"), from the
  name itself or its `name:ja-Hira` / `name:ja-Kana` reading. Kanji are never read: their reading
  in a place name cannot be told from the characters (槍ヶ岳 is Yarigatake), and a dictionary guess
  printed "yari ke gaku". A Chinese reading of a Japanese name would be worse still, so Han
  characters in Japan get nothing.

Scripts without written vowels (Arabic, Hebrew) and those not listed get nothing: the local name is
shown as it is.
"""

import unicodedata

from anyascii import anyascii

# The first word of the Unicode character name of the letters each rule handles.
_ALPHABETS = {"CYRILLIC", "GREEK", "ARMENIAN", "GEORGIAN"}

# A trailing character that is a generic word ("mountain", "peak", "ridge"), written apart.
_HAN_GENERIC = set("山峰岭嶺顶頂岳嶽岩尖坡梁台臺崗岗峯")

_JAPANESE_READING_KEYS = ("name:ja-hira", "name:ja-kana", "name:ja_kana")


def latin_spelling(name, tags, region):
    """A Latin spelling of `name`, or "" when there is no trustworthy one.

    `region` is the mapsforge file the peak came from without `.poi` (`japan`, `bulgaria`, ...):
    the same characters are read differently in Japan and China, and in Bulgaria and Russia.
    """
    scripts = _scripts(name)
    foreign = scripts - {"LATIN"}
    if not foreign:
        return ""
    japanese = region == "japan"
    if japanese:
        reading = next((v for k, v in tags.items() if k.lower() in _JAPANESE_READING_KEYS and v), "")
        if reading and _scripts(reading) <= {"HIRAGANA", "KATAKANA", "LATIN"}:
            return _kana(reading)
        if foreign <= {"HIRAGANA", "KATAKANA"}:
            return _kana(name)
        return ""
    if foreign <= {"CJK"}:
        return _han(name)
    if foreign <= {"HANGUL"}:
        return _words(_hangul_word(w) if _scripts(w) - {"LATIN"} else w for w in name.split())
    if foreign <= _ALPHABETS:
        return _alphabet(name, region)
    return ""


def _scripts(text):
    out = set()
    for ch in text:
        if unicodedata.category(ch).startswith("L"):
            out.add(unicodedata.name(ch, "?").split(" ")[0])
    return out


def _words(words):
    """Capitalised words, single spaces: "gora lebedev" -> "Gora Lebedev"."""
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


# --- Cyrillic, Greek, Armenian, Georgian -------------------------------------------------------

_GREEK_DIGRAPHS = (("ου", "ou"), ("αυ", "av"), ("ευ", "ev"), ("ηυ", "iv"))

_UKRAINIAN = {"г": "h", "ґ": "g", "и": "y", "і": "i", "ї": "yi", "є": "ye", "й": "y", "щ": "shch"}


def _alphabet(name, region):
    # Greek accents carry nothing in the Latin spelling, and without them the digraphs are plain. Only
    # Greek: in Cyrillic the marks are letters of their own (й is not и, ё is not е).
    text = "".join(_greek_unaccented(c) for c in name)
    for greek, latin in _GREEK_DIGRAPHS:
        text = text.replace(greek, latin).replace(greek.upper(), latin.upper()).replace(greek.capitalize(), latin.capitalize())
    if region == "ukraine":
        text = "".join(_cased(_UKRAINIAN.get(c.lower()), c) or c for c in text)
    if region == "bulgaria":
        text = text.replace("щ", "sht").replace("Щ", "Sht")
    out = []
    for i, c in enumerate(text):
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if c in "ъЪ":
            # Bulgarian: a vowel before a consonant. Russian: a separator before a vowel, silent.
            out.append(("a" if c == "ъ" else "A") if nxt.isalpha() and nxt.lower() not in "еёюяи" else "")
        elif c in "ьЬ":
            # Ukrainian and Russian soft sign: no letter of its own on a map.
            out.append("")
        elif c in "ёЁ":
            # anyascii writes "e"; maps write the sound, "yo" (Podyomnaya, Fyodorova).
            out.append("yo" if c == "ё" else "Yo")
        elif unicodedata.name(c, "").startswith("LATIN"):
            out.append(c)  # the Latin part of a two-script name keeps its diacritics ("Rožok/Ріжок")
        else:
            out.append(anyascii(c))
    return _words("".join(out).replace("'", "").split())


def _greek_unaccented(c):
    if not unicodedata.name(c, "").startswith("GREEK"):
        return c
    return "".join(d for d in unicodedata.normalize("NFD", c) if not unicodedata.combining(d))


def _cased(latin, original):
    if latin is None:
        return None
    return latin.capitalize() if original.isupper() else latin


# --- Chinese ------------------------------------------------------------------------------------

def _han(name):
    chars = [c for c in name if not c.isspace()]
    if len(chars) > 1 and chars[-1] in _HAN_GENERIC:
        head, tail = chars[:-1], chars[-1:]
    else:
        head, tail = chars, []
    return _words(["".join(anyascii(c).lower() for c in head), "".join(anyascii(c).lower() for c in tail)])


# --- Korean: Revised Romanization ---------------------------------------------------------------

_INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_VOWELS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we",
           "wi", "yu", "eu", "ui", "i"]
# Index of the final consonant -> how it sounds at the end of a syllable (k, n, t, l, m, p, ng).
_FINAL_SOUND = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p",
                "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]
# Index of the final consonant -> how it is read when the next syllable starts with a vowel (liaison).
_FINAL_LIAISON = ["", "g", "kk", "gs", "n", "nj", "nh", "d", "r", "lg", "lm", "lb", "ls", "lt", "lp", "lh",
                  "m", "b", "bs", "s", "ss", "ng", "j", "ch", "k", "t", "p", ""]
_SILENT_INITIAL = 11
_NIEUN, _RIEUL, _MIEUM = 2, 5, 6


def _hangul_word(word):
    syllables = []
    for ch in word:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            syllables.append((code // 588, code % 588 // 28, code % 28))
        else:
            syllables.append(ch)
    out = []
    for i, s in enumerate(syllables):
        if isinstance(s, str):
            out.append(anyascii(s))
            continue
        initial, vowel, final = s
        prev = syllables[i - 1] if i > 0 and not isinstance(syllables[i - 1], str) else None
        nxt = syllables[i + 1] if i + 1 < len(syllables) and not isinstance(syllables[i + 1], str) else None
        head = _INITIALS[initial]
        if prev is not None:
            sound = _FINAL_SOUND[prev[2]]
            if initial == _SILENT_INITIAL and prev[2] and sound != "ng":
                head = ""  # the previous final was carried over (liaison)
            elif initial == _RIEUL:
                head = "l" if sound in ("l", "n") else ("n" if sound else "r")
            elif initial == _NIEUN and sound == "l":
                head = "l"
        tail = ""
        if final:
            sound = _FINAL_SOUND[final]
            if nxt is not None and nxt[0] == _SILENT_INITIAL and sound != "ng":
                tail = _FINAL_LIAISON[final]
            elif nxt is not None and nxt[0] in (_NIEUN, _MIEUM, _RIEUL):
                nasal = {"k": "ng", "t": "n", "p": "m"}.get(sound, sound)
                tail = "l" if sound == "n" and nxt[0] == _RIEUL else nasal
            else:
                tail = sound
        out.append(head + _VOWELS[vowel] + tail)
    return "".join(out)


# --- Japanese kana: Hepburn ---------------------------------------------------------------------

_KANA = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ゔ": "vu", "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゎ": "wa",
}
_SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
_LONG_VOWELS = (("ou", "o"), ("oo", "o"), ("uu", "u"))


def _kana(text):
    # Katakana sits 0x60 above the hiragana of the same sound.
    hira = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)
    words = []
    for word in hira.replace("・", " ").split():
        out = []
        double = False
        for c in word:
            if c == "っ":
                double = True
                continue
            if c in _SMALL_Y and out:
                prev = out.pop()
                # き + ゃ -> kya, し + ゃ -> sha, ち + ゃ -> cha, じ + ゃ -> ja
                stem = prev[:-1]
                syllable = (stem if stem in ("sh", "ch", "j") else stem + "y") + _SMALL_Y[c]
            elif c == "ー":
                continue  # a long vowel: no mark without macrons
            else:
                syllable = _KANA.get(c, anyascii(c).lower())
            if double and syllable:
                syllable = ("t" if syllable.startswith("ch") else syllable[0]) + syllable
            double = False
            out.append(syllable)
        romaji = "".join(out)
        for long, short in _LONG_VOWELS:
            romaji = romaji.replace(long, short)
        words.append(romaji)
    return _words(words)
