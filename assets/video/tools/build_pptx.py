#!/usr/bin/env python3
"""Build an EDITABLE native PowerPoint of the Zyroflo investor deck.

This is NOT a picture export. Every title, code line, card, gate marker and
metric is a real PowerPoint text box / autoshape, so wording and numbers can be
edited in PowerPoint, and slides added or removed. It mirrors the seven HTML
slides in ``assets/video/deck`` -- their 1920x1080 layout maps 1:1 onto a 16:9
slide at 144 px/in (so px/144 = inches, px/2 = points) -- and drops each slide's
narration into the speaker notes, read from ``deck/script.js`` so the notes can
never drift from the video's single source of truth.

    python tools/build_pptx.py            # -> dist/zyroflo-deck.pptx

Fonts are Segoe UI + Consolas, present on every Windows PowerPoint, so the file
opens with no missing-font substitution. The design is deliberately "native
plain": no gradient/glow/animation from the HTML deck -- the accent second line
of each heading is rendered in solid neon pink instead of the CSS gradient.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent.parent
DECK = ROOT / "deck"
DIST = ROOT / "dist"

# ---- palette (from theme.css) --------------------------------------------
BG      = RGBColor.from_string("0A0E1A")
SURFACE = RGBColor.from_string("141A2E")
CODE_BG = RGBColor.from_string("0E1424")
TEXT    = RGBColor.from_string("E8ECF5")
MUTED   = RGBColor.from_string("9AA6C2")
DIM     = RGBColor.from_string("6B7699")
BRAND   = RGBColor.from_string("DB2777")
NEON    = RGBColor.from_string("FF3D9A")
PINKLT  = RGBColor.from_string("FF8FC7")
INDIGO  = RGBColor.from_string("4F46E5")
AMBER   = RGBColor.from_string("FBBF24")
GREEN   = RGBColor.from_string("34D399")
RED     = RGBColor.from_string("F87171")
BORDER  = RGBColor.from_string("2A3350")

# tinted fills (approximating the CSS rgba washes on an opaque dark bg)
PINK_WASH  = RGBColor.from_string("241127")
GREEN_WASH = RGBColor.from_string("112318")
RED_WASH   = RGBColor.from_string("241213")
AMBER_LINE = RGBColor.from_string("FBBF24")

# code syntax colours
C_COM = DIM
C_KEY = PINKLT
C_STR = GREEN
C_HL  = NEON
CODE  = RGBColor.from_string("C3CEF0")

SANS = "Segoe UI"
MONO = "Consolas"


def IN(px: float) -> Emu:
    return Inches(px / 144.0)


def PT(px: float) -> Pt:
    return Pt(px / 2.0)


# ---- primitives ----------------------------------------------------------
def add_slide(prs: Presentation):
    s = prs.slides.add_slide(prs.slide_layouts[6])          # blank
    fill = s.background.fill
    fill.solid()
    fill.fore_color.rgb = BG
    return s


def box(slide, x, y, w, h, *, fill=SURFACE, line=BORDER, line_w=1.0,
        radius=0.06, shape=MSO_SHAPE.ROUNDED_RECTANGLE, dash=None):
    sp = slide.shapes.add_shape(shape, IN(x), IN(y), IN(w), IN(h))
    sp.shadow.inherit = False
    if fill is None:
        sp.fill.background()
    else:
        sp.fill.solid()
        sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(line_w)
        if dash is not None:
            ln = sp.line._get_or_add_ln()
            ln.append(ln.makeelement(qn("a:prstDash"), {"val": dash}))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            sp.adjustments[0] = radius
        except Exception:
            pass
    return sp


def text(slide, x, y, w, h, paras, *, size=26, color=TEXT, bold=False,
         font=SANS, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0,
         wrap=True):
    """paras: a string, or a list of paragraphs; each paragraph is a string or
    a list of (text, opts) runs. opts keys: size, color, bold, font."""
    tb = slide.shapes.add_textbox(IN(x), IN(y), IN(w), IN(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.margin_left = tf.margin_right = Pt(2)
    tf.margin_top = tf.margin_bottom = Pt(2)
    tf.vertical_anchor = anchor
    if isinstance(paras, str):
        paras = [paras]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.line_spacing = spacing
        if isinstance(para, str):
            para = [(para, {})]
        for txt, opts in para:
            r = p.add_run()
            r.text = txt
            r.font.name = opts.get("font", font)
            r.font.size = PT(opts.get("size", size))
            r.font.bold = opts.get("bold", bold)
            r.font.color.rgb = opts.get("color", color)
    return tb


def diamond(slide, cx, cy, s, *, color=AMBER, glow=False):
    d = box(slide, cx - s / 2, cy - s / 2, s, s, fill=BG, line=color,
            line_w=2.0, shape=MSO_SHAPE.DIAMOND)
    return d


def chip(slide, x, y, label, *, fs=24, fill=SURFACE, fg=TEXT, border=BORDER):
    w = 34 + int(len(label) * fs * 0.60)
    h = 52
    box(slide, x, y, w, h, fill=fill, line=border, radius=0.5)
    text(slide, x, y, w, h, [[(label, {"color": fg, "size": fs})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return w


def code_window(slide, x, y, w, h, title, lines, *, fs=21):
    box(slide, x, y, w, h, fill=CODE_BG, line=BORDER, radius=0.04)
    for i, c in enumerate((RED, AMBER, GREEN)):
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL,
                                     IN(x + 22 + i * 22), IN(y + 20), IN(13), IN(13))
        dot.shadow.inherit = False
        dot.fill.solid()
        dot.fill.fore_color.rgb = c
        dot.line.fill.background()
    text(slide, x + 96, y + 6, w - 110, 34,
         [[(title, {"color": MUTED, "size": 19, "font": MONO})]],
         anchor=MSO_ANCHOR.MIDDLE)
    tb = slide.shapes.add_textbox(IN(x + 22), IN(y + 50), IN(w - 40), IN(h - 60))
    tf = tb.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = Pt(2)
    tf.margin_top = Pt(2)
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = 1.28
        if not ln:
            r = p.add_run(); r.text = " "; r.font.size = PT(fs); r.font.name = MONO
            continue
        for txt, col in ln:
            r = p.add_run()
            r.text = txt
            r.font.name = MONO
            r.font.size = PT(fs)
            r.font.color.rgb = col
    return tb


# ---- shared furniture ----------------------------------------------------
def bug(slide, eyebrow):
    text(slide, 120, 56, 1300, 40,
         [[("Zyroflo", {"color": NEON, "bold": True, "size": 24}),
           ("   ·   " + eyebrow, {"color": MUTED, "size": 24})]],
         anchor=MSO_ANCHOR.MIDDLE)


def heading(slide, y, line1, accent, size=62):
    text(slide, 120, y, 1680, 140,
         [[(line1 + " ", {"color": TEXT, "bold": True, "size": size}),
           (accent, {"color": NEON, "bold": True, "size": size})]], spacing=1.04)


def landing(slide, y, main, accent, *, size=42):
    text(slide, 120, y, 1700, 96,
         [[(main + " ", {"color": TEXT, "bold": True, "size": size}),
           (accent, {"color": NEON, "bold": True, "size": size})]], spacing=1.02)


def label(slide, x, y, w, s, *, color=MUTED, size=20, align=PP_ALIGN.LEFT,
          bold=True, spacing=.16):
    # small uppercase caption
    text(slide, x, y, w, 34, [[(s.upper(), {"color": color, "size": size, "bold": bold})]],
         align=align, anchor=MSO_ANCHOR.MIDDLE)


# ---- rail helpers --------------------------------------------------------
def rail_phase(slide, x, ytop, w, name, art, doer, *, hot=False):
    line = NEON if hot else BORDER
    fill = PINK_WASH if hot else SURFACE
    box(slide, x, ytop, w, 78, fill=fill, line=line, radius=0.14)
    text(slide, x, ytop, w, 78, [[(name, {"size": 26})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(slide, x - 6, ytop + 84, w + 12, 26,
         [[(art, {"color": PINKLT, "size": 19, "font": MONO})]], align=PP_ALIGN.CENTER)
    text(slide, x - 6, ytop + 110, w + 12, 24,
         [[(doer, {"color": DIM, "size": 18})]], align=PP_ALIGN.CENTER)


def rail_gate(slide, cx, ytop, tag, *, hard=False):
    color = NEON if hard else AMBER
    diamond(slide, cx, ytop + 26, 24, color=color)
    text(slide, cx - 26, ytop + 48, 52, 22,
         [[(tag, {"color": color, "size": 17, "bold": True})]],
         align=PP_ALIGN.CENTER)


# =========================================================================
#   SLIDES
# =========================================================================
def slide00(prs):
    s = add_slide(prs)
    text(s, 0, 300, 1920, 40,
         [[("C B E Y O N D   T E C H N O L O G I E S", {"color": MUTED, "size": 22, "bold": True})]],
         align=PP_ALIGN.CENTER)
    text(s, 0, 356, 1920, 190, [[("Zyroflo", {"color": NEON, "size": 152, "bold": True})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    box(s, 910, 556, 100, 4, fill=NEON, line=None, radius=0.5)
    text(s, 200, 592, 1520, 80,
         [[("Conquer the next frontier with ", {"size": 46, "bold": True}),
           ("AI-driven SDLC.", {"color": NEON, "size": 46, "bold": True})]],
         align=PP_ALIGN.CENTER)
    return s


def slide01(prs):
    s = add_slide(prs)
    bug(s, "The problem")
    heading(s, 168, "AI can build the whole feature now.", "In an afternoon.")

    steps = ["design", "code", "tests", "deploy"]
    sw, gap = 372, 40
    x = 120
    for i, st in enumerate(steps):
        box(s, x, 336, sw, 92, fill=PINK_WASH, line=NEON, radius=0.16)
        text(s, x, 336, sw, 92, [[(st, {"size": 30})]],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        if i < 3:
            text(s, x + sw, 336, gap, 92, [[("→", {"color": DIM, "size": 30})]],
                 align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        x += sw + gap
    text(s, 120, 446, 1680, 30,
         [[("…with nobody in the loop.", {"color": DIM, "size": 26})]])

    qs = ["Who approved it?", "Which model wrote it?", "Could you prove it to an auditor?"]
    cw, cgap = 536, 36
    x = 120
    for q in qs:
        box(s, x, 520, cw, 176, fill=SURFACE, line=BORDER, radius=0.10)
        text(s, x + 30, 544, cw - 56, 82, [[(q, {"size": 33})]], spacing=1.05)
        box(s, x + 30, 636, 168, 40, fill=RED_WASH, line=RED, radius=0.28)
        text(s, x + 30, 636, 168, 40, [[("NO RECORD", {"color": RED, "size": 18, "bold": True})]],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        x += cw + cgap
    landing(s, 800, "Speed stopped being the problem.", "Ungoverned speed is.", size=44)
    return s


def slide02(prs):
    s = add_slide(prs)
    bug(s, "One ticket, end to end")
    # illustrative tag, top-right
    box(s, 1470, 56, 330, 44, fill=SURFACE, line=BORDER, radius=0.4)
    text(s, 1470, 56, 330, 44,
         [[("PROJ-482 · illustrative", {"color": MUTED, "size": 19, "font": MONO})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    heading(s, 150, "A ticket in.", "Shipped, audited software out.")

    code_window(s, 120, 262, 1680, 190, "your repo", [
        [("# a ticket from any tracker — or one line of plain English", C_COM)],
        [("$ /sdlc PROJ-482", CODE)],
        [("→ ", C_KEY), ("pulled ", CODE),
         ("\"CSV importer throws on empty rows\"", C_STR), (" from your tracker", CODE)],
    ], fs=24)

    # rail: 6 phases, 5 gates
    phases = [
        ("intake", "00-intake.md", "elaborates it"),
        ("requirements", "10-requirements.md", "asks, then waits"),
        ("design", "30-design.md", "options + a pick"),
        ("implement", "40-impl/", "writes the code"),
        ("test", "50-tests.md", "verifies each one"),
        ("deploy", "90-deploy.md", "ships it"),
    ]
    gates = [("G1", False), ("G2", False), ("G3", False), ("G4", True), ("G5", False)]
    pw, gw, ytop = 236, 52, 520
    x = 120
    for i, (name, art, doer) in enumerate(phases):
        rail_phase(s, x, ytop, pw, name, art, doer)
        x += pw
        if i < 5:
            tag, hard = gates[i]
            rail_gate(s, x + gw / 2, ytop, tag, hard=hard)
            x += gw

    text(s, 120, 700, 1680, 34,
         [[("Twelve phases available — you run the ones the work needs.  ",
            {"color": MUTED, "size": 30}),
           ("A human stands at every gate.", {"color": TEXT, "size": 30, "bold": True})]])
    landing(s, 784, "The same loop ships a one-line fix —", "or an entire product.")
    return s


def slide03(prs):
    s = add_slide(prs)
    bug(s, "Strict human governance")
    heading(s, 150, "AI proposes.", "A human disposes.")

    gates = [("G1", "scope", False), ("G2", "design", False),
             ("G3", "the agent’s changes", False), ("G4", "the merge", True),
             ("G5", "the deploy", False)]
    cw, gap = 316, 22
    x = 120
    for tag, lab, hard in gates:
        line = NEON if hard else BORDER
        fill = PINK_WASH if hard else SURFACE
        box(s, x, 322, cw, 190 if hard else 168, fill=fill, line=line, radius=0.10)
        diamond(s, x + cw / 2, 360, 30, color=(NEON if hard else AMBER))
        text(s, x, 386, cw, 44, [[(tag, {"color": (NEON if hard else AMBER),
                                          "size": 30, "bold": True})]],
             align=PP_ALIGN.CENTER)
        text(s, x + 8, 432, cw - 16, 40, [[(lab, {"color": MUTED, "size": 22})]],
             align=PP_ALIGN.CENTER)
        if hard:
            text(s, x, 476, cw, 30, [[("hard in every regime",
                                       {"color": NEON, "size": 17, "bold": True})]],
                 align=PP_ALIGN.CENTER)
        x += cw + gap

    # two action cards
    aw, agap = 826, 28
    box(s, 120, 552, aw, 116, fill=GREEN_WASH, line=GREEN, radius=0.09)
    text(s, 150, 566, aw - 60, 40, [[("Approve → it continues", {"color": GREEN, "size": 30, "bold": True})]])
    text(s, 150, 606, aw - 60, 50, [[("The next phase starts the moment the gate clears.",
                                      {"color": MUTED, "size": 24})]])
    box(s, 120 + aw + agap, 552, aw, 116, fill=RED_WASH, line=RED, radius=0.09)
    text(s, 150 + aw + agap, 566, aw - 60, 40, [[("Reject → it loops back", {"color": RED, "size": 30, "bold": True})]])
    text(s, 150 + aw + agap, 606, aw - 60, 50,
         [[("The pipeline does not move until you say so. The loop-back is recorded.",
            {"color": MUTED, "size": 24})]])

    # CI refusal panel
    box(s, 120, 698, 1680, 70, fill=RED_WASH, line=RED, radius=0.12)
    x_cnt = slide_circle(s, 150, 715, RED)
    text(s, 200, 698, 1560, 70,
         [[("approval ledger — refused", {"color": RED, "size": 28, "bold": True}),
           ("    ·    a gate this run reached was never approved",
            {"color": TEXT, "size": 28})]], anchor=MSO_ANCHOR.MIDDLE)
    landing(s, 790, "A gate you can skip is only a suggestion.", "These, you cannot.", size=38)
    return s


def slide_circle(slide, x, y, color):
    c = slide.shapes.add_shape(MSO_SHAPE.OVAL, IN(x), IN(y), IN(36), IN(36))
    c.shadow.inherit = False
    c.fill.solid(); c.fill.fore_color.rgb = color
    c.line.fill.background()
    text(slide, x, y, 36, 36, [[("✕", {"color": BG, "size": 22, "bold": True})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return c


def slide04(prs):
    s = add_slide(prs)
    bug(s, "Yours to shape")
    heading(s, 146, "Your steps. Your gates.", "Your model for each one.")

    cols = ["intake", "requirements", "design", "implement", "test", "deploy"]
    cw, gut = 228, 34
    col_x = [120 + i * (cw + gut) for i in range(6)]
    gut_c = [col_x[i] + cw + gut / 2 for i in range(5)]   # gutter centres

    def lane(ytop, cap, cap_color, gutters, removed_idx=None, added_idx=None):
        label(s, 120, ytop, 400, cap, color=cap_color)
        by = ytop + 36
        for i, name in enumerate(cols):
            removed = (i == removed_idx)
            fill = None if removed else (PINK_WASH if cap_color == NEON else SURFACE)
            line = DIM if removed else (RGBColor.from_string("6E2B4E") if cap_color == NEON else BORDER)
            box(s, col_x[i], by, cw, 74, fill=fill, line=line, radius=0.16,
                dash="dash" if removed else None)
            text(s, col_x[i], by, cw, 74,
                 [[(name, {"size": 25, "color": (DIM if removed else TEXT)})]],
                 align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
            if removed:
                ln = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                            IN(col_x[i] + 26), IN(by + 37),
                                            IN(col_x[i] + cw - 26), IN(by + 37))
                ln.line.color.rgb = DIM; ln.line.width = Pt(1.5)
        for gi in range(5):
            if gutters[gi] == "arrow":
                text(s, gut_c[gi] - 17, by, 34, 74, [[("→", {"color": DIM, "size": 22})]],
                     align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
            elif gutters[gi] == "gate":
                diamond(s, gut_c[gi], by + 37, 22, color=AMBER)
            elif gutters[gi] == "added":
                diamond(s, gut_c[gi], by + 37, 24, color=NEON)
        return by

    lane(290, "Ships by default", MUTED,
         ["arrow", "arrow", "gate", "gate", "gate"])
    by2 = lane(470, "After your edits", NEON,
               ["arrow", "added", "gate", "gate", "gate"], removed_idx=4, added_idx=1)

    # callout labels for the two edits
    text(s, gut_c[1] - 90, by2 + 84, 180, 28,
         [[("↑ gate added", {"color": NEON, "size": 20, "bold": True})]], align=PP_ALIGN.CENTER)
    text(s, col_x[4] - 20, by2 + 84, cw + 40, 28,
         [[("↑ step removed", {"color": MUTED, "size": 20, "bold": True})]], align=PP_ALIGN.CENTER)
    # model names under design / implement / deploy
    for i, m in [(2, "gemini-2.5"), (3, "gpt-5"), (5, "claude-opus-5")]:
        text(s, col_x[i] - 10, by2 + 116, cw + 20, 26,
             [[(m, {"color": PINKLT, "size": 19, "font": MONO})]], align=PP_ALIGN.CENTER)

    cards = [
        ("In a wizard, or in chat", "Toggle steps and gates with a live preview of the pipeline you’re building."),
        ("Add your own step", "A plain markdown file with inputs and outputs splices straight into the line."),
        ("Floors you can only tighten", "Your organisation pins what nobody may loosen. Everything above that is yours."),
    ]
    bw, bgap = 544, 24
    x = 120
    for t, d in cards:
        box(s, x, 690, bw, 118, fill=SURFACE, line=BORDER, radius=0.09)
        text(s, x + 24, 704, bw - 48, 34, [[(t, {"size": 26, "bold": True})]])
        text(s, x + 24, 742, bw - 48, 60, [[(d, {"color": MUTED, "size": 21})]], spacing=1.1)
        x += bw + bgap
    landing(s, 826, "The same lifecycle fits a solo repo and", "a regulated bank.", size=34)
    return s


def slide05(prs):
    s = add_slide(prs)
    bug(s, "Who decided, when, why")
    heading(s, 148, "Every decision leaves", "a receipt.")

    code_window(s, 120, 286, 820, 258, "the ledger — every gate", [
        [("{ ", CODE), ("\"gate\"", C_KEY), (": ", CODE), ("\"G2\"", C_STR), (", ", CODE),
         ("\"decision\"", C_KEY), (": ", CODE), ("\"approved\"", C_STR), (",", CODE)],
        [("  ", CODE), ("\"approver\"", C_KEY), (": ", CODE), ("\"a.rahman\"", C_HL), (",", CODE)],
        [("  ", CODE), ("\"over_sha\"", C_KEY), (": ", CODE), ("\"4c91f0e\"", C_STR), (",", CODE)],
        [("  ", CODE), ("\"waited_ms\"", C_KEY), (": ", CODE), ("5400000", C_HL), (",", CODE)],
        [("  ", CODE), ("\"ledger_hash\"", C_KEY), (": ", CODE), ("\"c07b…\"", C_STR), (" }", CODE)],
    ], fs=20)
    code_window(s, 980, 286, 820, 258, "the run log — every step", [
        [("{ ", CODE), ("\"step\"", C_KEY), (": ", CODE), ("\"design\"", C_STR), (", ", CODE),
         ("\"model\"", C_KEY), (": ", CODE), ("\"gemini-2.5\"", C_HL), (",", CODE)],
        [("  ", CODE), ("\"tokens\"", C_KEY), (": { ", CODE), ("\"in\"", C_KEY), (": ", CODE),
         ("8140", C_HL), (", ", CODE), ("\"out\"", C_KEY), (": ", CODE), ("1920", C_HL), (" },", CODE)],
        [("  ", CODE), ("\"cost\"", C_KEY), (": ", CODE), ("0.031", C_HL), (", ", CODE),
         ("\"outcome\"", C_KEY), (": ", CODE), ("\"ok\"", C_STR), (",", CODE)],
        [("  ", CODE), ("\"loop_backs\"", C_KEY), (": ", CODE), ("1", C_HL), (",", CODE)],
        [("  ", CODE), ("\"artifacts\"", C_KEY), (": [", CODE), ("\"30-design.md\"", C_STR), ("] }", CODE)],
    ], fs=20)

    # illustrative badge
    box(s, 120, 566, 640, 42, fill=RGBColor.from_string("2A2410"), line=AMBER, radius=0.28)
    text(s, 120, 566, 640, 42,
         [[("◆ Illustrative · sample data, not a performance claim",
            {"color": AMBER, "size": 18, "bold": True})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

    mets = [("Lead time", "6.2 days", "↓ from 10.5"),
            ("Change failure rate", "4.1%", "↓ from 11.0%"),
            ("Rework per change", "0.4×", "↓ from 1.1×"),
            ("Gate wait (human)", "9.1 hrs", "↓ from 31.6")]
    mw, mgap = 403, 22
    x = 120
    for lab, val, delta in mets:
        box(s, x, 620, mw, 150, fill=SURFACE, line=BORDER, radius=0.09)
        label(s, x + 22, 636, mw - 40, lab, size=18)
        text(s, x + 22, 668, mw - 40, 60, [[(val, {"color": NEON, "size": 50, "bold": True})]])
        box(s, x + 22, 726, 150, 34, fill=GREEN_WASH, line=GREEN, radius=0.5)
        text(s, x + 22, 726, 150, 34, [[(delta, {"color": GREEN, "size": 18, "bold": True})]],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        x += mw + mgap
    landing(s, 800, "Measured on every run, in your own repository.", "Not a survey.", size=32)
    return s


def slide06(prs):
    s = add_slide(prs)
    bug(s, "No lock-in, no black box")
    heading(s, 158, "Any repo. Any AI tool.", "Any model — yours.")

    code_window(s, 120, 310, 760, 288, "one command", [
        [("# drop it into any repository", C_COM)],
        [("$ python sdlc-init.py", CODE)],
        [],
        [("✓ ", C_KEY), (".sdlc/profile.yml", CODE)],
        [("✓ ", C_KEY), (".sdlc/policy.yml", CODE)],
        [("✓ ", C_KEY), ("ledger check for CI", CODE)],
    ], fs=24)

    label(s, 944, 316, 856, "…and it writes itself into whatever you already use", size=20)
    tools = ["Claude Code", "Cursor", "Copilot", "Codex", "Windsurf", "Gemini", "Aider"]
    x, y = 944, 366
    for i, t in enumerate(tools):
        if i == 4:
            x, y = 944, 430
        w = chip(s, x, y, t, fs=24)
        x += w + 14

    box(s, 120, 604, 1680, 122, fill=PINK_WASH, line=BRAND, radius=0.07)
    text(s, 150, 620, 1620, 46,
         [[("The harness itself ", {"size": 36, "bold": True}),
           ("never calls a model.", {"color": NEON, "size": 36, "bold": True})]])
    text(s, 150, 672, 1620, 40,
         [[("Your keys, your providers, your spend. Zyroflo governs the work — it never does the spending.",
            {"color": MUTED, "size": 24})]])

    badges = [("No third-party packages", "standard library only"),
              ("No network", "install runs entirely offline"),
              ("No API keys", "nothing to hand over to install")]
    bw, bgap = 544, 24
    x = 120
    for t, d in badges:
        box(s, x, 748, bw, 96, fill=SURFACE, line=BORDER, radius=0.10)
        text(s, x, 762, bw, 34, [[(t, {"size": 27, "bold": True})]], align=PP_ALIGN.CENTER)
        text(s, x, 798, bw, 30, [[(d, {"color": MUTED, "size": 19})]], align=PP_ALIGN.CENTER)
        x += bw + bgap
    text(s, 120, 858, 1680, 34,
         [[("…and that promise is a test, not a claim.", {"color": MUTED, "size": 26})]])
    return s


def slide07(prs):
    s = add_slide(prs)
    text(s, 0, 176, 1920, 170, [[("Zyroflo", {"color": NEON, "size": 150, "bold": True})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0, 360, 1920, 60,
         [[("AI-driven delivery. ", {"size": 44, "bold": True}),
           ("Human-governed.", {"color": NEON, "size": 44, "bold": True})]],
         align=PP_ALIGN.CENTER)

    box(s, 330, 452, 1260, 236, fill=RGBColor.from_string("1B1330"), line=NEON,
        line_w=1.5, radius=0.06)
    text(s, 330, 478, 1260, 34,
         [[("BECOME A DESIGN PARTNER", {"color": NEON, "size": 22, "bold": True})]],
         align=PP_ALIGN.CENTER)
    text(s, 330, 512, 1260, 90,
         [[("cbeyond.cloud/zyroflo", {"color": NEON, "size": 66, "bold": True, "font": MONO})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    # three numbered chips, centred
    labels = [("① Visit the page", False), ("② Contact", False), ("③ Ask for a slot", True)]
    widths = [34 + int(len(t) * 24 * 0.60) for t, _ in labels]
    total = sum(widths) + 2 * 44
    x = 960 - total / 2
    for i, (t, fill) in enumerate(labels):
        if fill:
            chip(s, x, 628, t, fs=24, fill=NEON, fg=RGBColor.from_string("2A0616"), border=NEON)
        else:
            chip(s, x, 628, t, fs=24)
        x += widths[i]
        if i < 2:
            text(s, x, 628, 44, 52, [[("→", {"color": DIM, "size": 26})]],
                 align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
            x += 44

    text(s, 0, 752, 1920, 34,
         [[("First cohort deliberately small", {"color": MUTED, "size": 24})]],
         align=PP_ALIGN.CENTER)
    logo = DECK / "assets" / "logo-header.png"
    if logo.exists():
        text(s, 0, 812, 900, 44, [[("by", {"color": DIM, "size": 22})]],
             align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
        pic = s.shapes.add_picture(str(logo), IN(970), IN(810), height=IN(48))
        pic.left = Emu(int((prs.slide_width - pic.width) / 2) + IN(30))
    else:
        text(s, 0, 812, 1920, 40, [[("by Cbeyond Technologies", {"color": DIM, "size": 22})]],
             align=PP_ALIGN.CENTER)
    return s


def set_notes(slide, sc):
    tf = slide.notes_slide.notes_text_frame
    parts = []
    n = sc.get("narration", "").strip()
    if n:
        parts.append(n)
    if sc.get("notes"):
        if parts:
            parts.append("")
        parts.append("— Presenter notes —")
        parts += [f"• {x}" for x in sc["notes"]]
    tf.text = "\n".join(parts)


def load_scenes():
    js = ("const fs=require('fs');global.window={};"
          f"eval(fs.readFileSync({json.dumps(str(DECK / 'script.js'))},'utf8'));"
          "process.stdout.write(JSON.stringify(window.SCENES));")
    out = subprocess.run(["node", "-e", js], capture_output=True, check=True).stdout
    return {s["id"]: s for s in json.loads(out.decode("utf-8"))}


def main() -> int:
    scenes = load_scenes()
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    builders = [("s00", slide00), ("s01", slide01), ("s02", slide02), ("s03", slide03),
                ("s04", slide04), ("s05", slide05), ("s06", slide06), ("s07", slide07)]
    for sid, fn in builders:
        slide = fn(prs)
        if sid in scenes:
            set_notes(slide, scenes[sid])

    DIST.mkdir(exist_ok=True)
    out = DIST / "zyroflo-deck.pptx"
    prs.save(str(out))
    print(f"{out}  ({out.stat().st_size / 1024:.0f} KB, {len(prs.slides._sldIdLst)} slides)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
