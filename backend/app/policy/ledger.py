"""Context Ledger: every piece of untrusted content the agent ingests in a cycle.

News posts, tweets, x402 responses and market descriptions are registered here
with a source label before the LLM sees them. When the LLM later proposes an
action, the policy engine asks the ledger whether any action parameter can be
traced back to that content.

Attackers hide instructions, so each item is expanded into "variants": the raw
text, a normalized form (invisible characters removed, Unicode tag characters
and compatibility forms folded to ASCII), a form with whitespace removed, and
decoded forms (base64, hex, Morse, reversed), two levels deep. A match in any
variant counts, and the finding says which one.

This is a tripwire, not a proof. A parameter the attacker manages to smuggle
past it is still subject to every other rule and to the onchain caps.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MIN_NEEDLE = 6
MAX_VARIANTS_PER_ITEM = 40
MAX_DECODE_DEPTH = 2

_INVISIBLE = re.compile(
    "[­͏؜ᅟᅠ឴឵᠎​-‏‪-‮"
    "⁠-⁯ㅤ︀-️﻿ﾠ]"
)
_TAG_BASE = 0xE0000
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "·": ".", "•": "."})

_BASE64_RUN = re.compile(r"[A-Za-z0-9+/_\-]{16,}={0,2}")
_HEX_RUN = re.compile(r"(?:0x)?((?:[0-9a-fA-F]{2}){8,})")
_MORSE_RUN = re.compile(r"(?:[.\-]{1,6}[ /|]+){5,}[.\-]{1,6}")

_MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F",
    "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
    "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z", "-----": "0", ".----": "1", "..---": "2",
    "...--": "3", "....-": "4", ".....": "5", "-....": "6", "--...": "7",
    "---..": "8", "----.": "9", ".-.-.-": ".", "--..--": ",", "---...": ":",
    "-..-.": "/", "-....-": "-", ".--.-.": "@", "...-..-": "$",
}

_NUMBER = r"(\d[\d,]*(?:\.\d+)?)"
_AMOUNT_PATTERNS = [
    re.compile(r"\$\s?" + _NUMBER),
    re.compile(
        _NUMBER + r"\s*(?:usdc|usd|dollars?|bucks|contracts?|shares?|yes|no|sol)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:buy|sell|send|transfer|pay|bid|order|purchase|wire|tip|deposit|withdraw)"
        r"\s+(?:exactly\s+|up\s+to\s+|another\s+)?\$?" + _NUMBER,
        re.IGNORECASE,
    ),
]


def normalize(text: str) -> str:
    """Fold hidden and look-alike characters to plain text."""
    folded = []
    for ch in text:
        cp = ord(ch)
        if 0xE0020 <= cp <= 0xE007E:  # Unicode tag characters: invisible ASCII
            folded.append(chr(cp - _TAG_BASE))
        elif 0xE0000 <= cp <= 0xE007F:
            continue
        else:
            folded.append(ch)
    text = _INVISIBLE.sub("", "".join(folded))
    return unicodedata.normalize("NFKC", text).translate(_DASHES)


def _printable(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(text) < 4:
        return None
    ok = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return text if ok / len(text) >= 0.9 else None


def _base64_decodings(text: str) -> list[str]:
    found = []
    for run in _BASE64_RUN.findall(text):
        body = run.rstrip("=")
        padded = body + "=" * (-len(body) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = _printable(decoder(padded))
            except (binascii.Error, ValueError):
                continue
            if decoded:
                found.append(decoded)
                break
    return found


def _hex_decodings(text: str) -> list[str]:
    found = []
    for run in _HEX_RUN.findall(text):
        decoded = _printable(bytes.fromhex(run))
        if decoded:
            found.append(decoded)
    return found


def _morse_decodings(text: str) -> list[str]:
    found = []
    for match in _MORSE_RUN.finditer(text):
        words = re.split(r"\s*[/|]\s*|\s{3,}", match.group(0).strip())
        decoded_words = []
        for word in words:
            letters = [_MORSE.get(symbol, "") for symbol in word.split()]
            decoded_words.append("".join(letters))
        decoded = " ".join(w for w in decoded_words if w)
        if len(decoded) >= 4:
            found.append(decoded)
    return found


def expand_variants(text: str) -> tuple[tuple[str, str], ...]:
    """All readings of `text` an attacker could be relying on: (label, text)."""
    variants: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, value: str) -> bool:
        if not value or value in seen or len(variants) >= MAX_VARIANTS_PER_ITEM:
            return False
        seen.add(value)
        variants.append((label, value))
        return True

    def explore(label: str, value: str, depth: int) -> None:
        if not add(label, value):
            return
        prefix = "" if label == "plain text" else f"{label}, then "
        compact = re.sub(r"\s+", "", value)
        add(f"{prefix}whitespace removed", compact)
        add(f"{prefix}reversed", value[::-1])
        if depth >= MAX_DECODE_DEPTH:
            return
        for decoded in _base64_decodings(value) + _base64_decodings(compact):
            explore(f"{prefix}base64-decoded", normalize(decoded), depth + 1)
        for decoded in _hex_decodings(value):
            explore(f"{prefix}hex-decoded", normalize(decoded), depth + 1)
        for decoded in _morse_decodings(value):
            explore(f"{prefix}Morse-decoded", decoded, depth + 1)

    normalized = normalize(text)
    if normalized != text:
        add("raw text", text)
        explore("hidden characters removed", normalized, 0)
    else:
        explore("plain text", text, 0)
    return tuple(variants)


def extract_amounts(text: str) -> frozenset[Decimal]:
    """Numbers that read as money or order sizes ("$40", "40 YES", "send 40")."""
    amounts: set[Decimal] = set()
    for pattern in _AMOUNT_PATTERNS:
        for raw in pattern.findall(text):
            try:
                value = Decimal(raw.replace(",", ""))
            except InvalidOperation:
                continue
            if value > 0:
                amounts.add(value.normalize())
    return frozenset(amounts)


@dataclass(frozen=True)
class LedgerItem:
    item_id: str
    source: str
    text: str
    variants: tuple[tuple[str, str], ...]
    # (amount, label of the variant it was first seen in)
    amounts: tuple[tuple[Decimal, str], ...]


@dataclass(frozen=True)
class TaintMatch:
    item_id: str
    source: str
    via: str

    def describe(self) -> str:
        where = f"untrusted {self.source} item #{self.item_id}"
        return where if self.via == "plain text" else f"{where} ({self.via})"


@dataclass(frozen=True)
class LedgerSnapshot:
    """Immutable view of the ledger handed to the policy engine."""

    items: tuple[LedgerItem, ...] = ()

    def find_text(self, needle: str) -> TaintMatch | None:
        """Does `needle` occur in any reading of any untrusted item?"""
        needle = normalize(needle).strip()
        if len(needle) < MIN_NEEDLE:
            return None
        lowered = needle.lower()
        for item in self.items:
            for via, text in item.variants:
                if needle in text:
                    return TaintMatch(item.item_id, item.source, via)
            # Morse and similar encodings lose letter case.
            for via, text in item.variants:
                if "Morse" in via and lowered in text.lower():
                    return TaintMatch(item.item_id, item.source, f"{via}, case-insensitive")
        return None

    def find_amount(self, amount: Decimal) -> TaintMatch | None:
        target = amount.normalize()
        for item in self.items:
            for value, via in item.amounts:
                if value == target:
                    label = "amount mentioned in the text"
                    if via != "plain text":
                        label = f"{label}, {via}"
                    return TaintMatch(item.item_id, item.source, label)
        return None


class ContextLedger:
    """Mutable per-cycle ledger owned by the agent runtime."""

    def __init__(self) -> None:
        self._items: list[LedgerItem] = []

    def register(self, source: str, item_id: str, text: str) -> LedgerItem:
        """Record one untrusted item. `source` is a label such as "x" or "news"."""
        variants = expand_variants(text)
        amounts: dict[Decimal, str] = {}
        for via, variant in variants:
            for amount in sorted(extract_amounts(variant)):
                amounts.setdefault(amount, via)
        item = LedgerItem(str(item_id), source, text, variants, tuple(amounts.items()))
        self._items.append(item)
        return item

    def snapshot(self) -> LedgerSnapshot:
        return LedgerSnapshot(tuple(self._items))

    def __len__(self) -> int:
        return len(self._items)
