Visual evidence for the D3 regression the round-1 review found, and its fix.

WHAT TO LOOK AT
  rail-before-6x.png      wt/t_a15acd97 at 85c079c6 (round 1), mid-page at 1440. The two
                          group-label dots ("Bills", "Debates and other business") are smaller
                          AND sit about a pixel left of the 8px dots above and below them.
  rail-after-6x.png       the same strip at HEAD. Every dot is on one column.
  railstrip-before-6x.png / railstrip-after-6x.png
                          the same comparison over the WHOLE tick list (all 9), so the row
                          rhythm is in one frame.
  *.png (no -6x)          the same crops at 1:1, i.e. how a reader actually sees it.

Every pair is the same clip from the same instrument at the same width, so the before and after
are directly comparable. The `-6x` files are Page.captureScreenshot clips at scale 6.

THE TWO SYMPTOMS, MEASURED -- THEY SHARE ONE CAUSE

The review reported (a) the level-3 dot 1px off the column and (b) a "21/22px uneven" vertical
pitch. Both are real, and both came from ONE thing: the level-3 rules set a per-level SIZE, and
the level-3 tick row was 6px tall where the level-2 row was 8px.

Read from the DOM (workspace probe_pitch.py; LAYOUT box = offsetWidth/Height, RENDERED box =
getBoundingClientRect, which also carries the active tick's transient scale):

                          BEFORE (85c079c6)                 AFTER (HEAD)
  mark layout box         6x6 at level 3, 8x8 elsewhere    8x8 at EVERY level
  tick row height         6px at level 3, 8px elsewhere    8px at EVERY level
  mark centres            1239 and 1240                    1240 only
  centre spread           1.00px (SAWTOOTH)                0.00px
  row pitch @1440         [22, 20, 22, 20, 22, 22, 22, 22] [22, 22, 22, 22, 22, 22, 22, 22]

The pitch alternated because the ticks are a column with a FIXED 14px gap: 14 + 8 = 22 on a
level-2 row, 14 + 6 = 20 on a level-3 row. That is the whole of symptom (b), and it is why
pinning the mark box alone would not have been enough -- the row height had to be pinned too.

Read from the PIXELS (workspace read_dots.py, stdlib PNG decode, scans only the rail's x-band so
text glyphs are not counted as dots, and rejects the progress fill by aspect ratio) -- this is an
independent instrument that shares no code with the DOM probe, and it agrees:

                          BEFORE (rail-before.png)          AFTER (rail-after.png)
  dot widths              6, 8, 8, 8, 8, 8                6, 8, 8, 8, 8, 8
  centre-x spread         1.0px                             0.0px
  centre pitch            21, 22, 22, 22, 22               22, 22, 22, 22, 22

Note the 6px dot is still 6px AFTER the fix -- that is deliberate. The level is PAINTED
(`transform:scale(.75)`), not LAID OUT, so the group dot is smaller while its box, its offset in
the tick, and the row height are all identical to every other level.

WHAT CHANGED
  site/build_site.py
      `.section-rail-tick-mark` is one 8px box at EVERY level. The level-3 size difference is a
      transform on that box. The mark is the FIRST item of a `justify-content:flex-start` flex row
      on desktop (and a `flex-end` row on a phone), so the mark's BOX is what decides where the
      dot lands -- a narrower box moved the dot's centre while every layout edge stayed put.
      `.section-rail-tick.level-2,.level-3{height:8px}` pins the row height.

  tools/check_outline.py
      D3 now asserts four facts per width, and the last one is the one the round-1 handoff had no
      assertion for: the 9 dot centres share one x; every mark's LAYOUT box is identical at every
      level; every mark sits at the same offset inside its tick; every tick is the same ROW
      HEIGHT. Runs at 390/760/1024/1280/1440/1920.
      Measured -- FAILS on 85c079c6 (3 items per phone width, 4 per desktop width, including the
      row height at 1024/1280/1440/1920), PASSES here.

  tools/check_rail_geometry.py
      The tool now EXITS NON-ZERO on D1/D3/D4 failure. Before this round it was a report with no
      exit code, which is exactly how "D3 dotXs=[1240, 1239] spread=1 SAWTOOTH" could sit in a
      saved log while the handoff claimed the geometry checks were OK. On this tree it exits 1 on
      85c079c6 and 0 on HEAD and on the pristine control.

REPRODUCE
  python3 tools/check_outline.py <base>                              # D3, at six widths
  python3 tools/check_rail_geometry.py <base> 1024,1280,1440,1920    # now with an exit code
