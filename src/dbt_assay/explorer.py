"""The explorer: one self-contained file holding everything assay knows, with tabs.

*** WHY ONE FILE AND NOT A DIRECTORY, WHICH IS NOT A STYLE PREFERENCE. ***
Browsers block `fetch` and XHR on `file://` URLs. A directory of HTML plus JSON that loads a
model when you click it cannot be opened by double-clicking it -- that option does not exist
locally, so an artifact you open from disk must carry its data inside it. The moment lazy loading
is wanted, a server is required; there is no middle rung. Measured at roughly 24 KB per model, so
a 358-model warehouse is about 9 MB, which opens instantly, and a 2,000-model one would not.

*** SO THE DATA SOURCE IS ONE SWAPPABLE LINE. ***
Everything expensive here -- the tables, the filtering, the model detail -- reads from one object
called DATA. Embedded, that object is parsed out of a script tag. Served, it would be one `fetch`.
Building the file therefore builds most of the server, and the decision to add `assay serve` can
be made later for a fraction of this work rather than a rewrite.

*** AND IT STAYS DETERMINISTIC. ***
No wall clock, no random, and every array arrives pre-sorted from `explore.assemble`. Two runs
against an unchanged store write identical bytes. What changed is that the FILE being identical
no longer means the RENDERED page is, because script decides what is shown -- which is why the
sorting lives in the assembly layer where a test can assert it, and not in the browser.
"""
from __future__ import annotations

import hashlib
import html
import json

CSS = """/* *** A PRINTED ASSAY REPORT, NOT A DASHBOARD. ***
   The whole surface is paper, ink and rules. There are no cards, no shadows, no rounded corners
   and no fills: a 17th-century plate book separates things with a line and with space, and that
   is also the honest way to render a page whose content is text and numbers.

   Colour does one job here and it is not decoration. The three forge tones mark a QUANTITY --
   a bar, a band, a share -- and nothing else on the page is ever coloured, so colour always means
   "this is a measurement" rather than "this is important". */
:root{
  --paper:#faf8f3;     /* laid paper */
  --ink:#1a1714;       /* the ink, warmer than black, which is what printed black looks like */
  --ash:#615a52;       /* secondary text */
  --faint:#948c81;     /* tertiary, and anything absent */
  --rule:#cec5b6;      /* hairline */
  --rule2:#e3dbcd;     /* the lighter rule, between rows */
  --rust:#a8491a;      /* the strongest measure */
  --ember:#d2833a;     /* the middle */
  --iron:#4a443d;      /* the weakest, and anything unsettled */
  --lin-h:460px;
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.55 "Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
display:flex;flex-direction:column;height:100vh;overflow:hidden;
-webkit-font-smoothing:antialiased}
header,footer{flex:0 0 auto}

/* The masthead: the mark, the name, and a double rule under the whole thing. Nothing else. */
header{padding:16px 26px 0;background:var(--paper);border-bottom:3px double var(--ink)}
h1{margin:0;font-family:Fell,"Iowan Old Style",Georgia,serif;font-weight:400;font-size:27px;
letter-spacing:.01em;display:flex;align-items:center;gap:11px}
/* The mark is the cut itself, so it blends onto the paper rather than sitting on a
   white field of its own. */
h1 > .mark{width:26px;height:auto;flex:none;mix-blend-mode:multiply}
h1 span.hname{flex:none;font-size:27px;color:var(--ink)}
h1 span{font-size:15px;color:var(--ash);font-style:italic;font-family:Fell,Georgia,serif}
.sub{font-size:12px;color:var(--faint);margin:3px 0 0;letter-spacing:.01em;
font-family:Fell,Georgia,serif}
.sub a{color:var(--rust)}

/* The tab strip reads as a running head: small caps, generous tracking, a rule under the lot and
   a heavy rule under the one you are on. */
nav{display:flex;gap:0;flex-wrap:wrap;margin-top:11px}
nav button{appearance:none;border:0;border-bottom:3px solid transparent;background:none;
font-family:Fell,Georgia,serif;font-size:15px;letter-spacing:.06em;text-transform:uppercase;
color:var(--ash);padding:7px 15px 6px;cursor:pointer;margin-bottom:-3px}
nav button:hover{color:var(--ink)}
nav button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--ink)}
nav button b{font-weight:400;color:var(--faint);margin-left:6px;font-size:12px;
letter-spacing:0;text-transform:none}

main{padding:20px 26px 22px;max-width:1560px;width:100%;flex:1 1 auto;min-height:0}
.panel{height:100%;overflow:auto}
.panel[hidden]{display:none}
@media (max-height:640px){:root{--lin-h:320px}}

/* ---- the plates. A cut sits in the page the way it sits in a book: alone, centred in its
   column, with a lettered caption under it in italic. */
.cut{display:block;max-width:100%;height:auto;image-rendering:crisp-edges;
mix-blend-mode:multiply}
.plate{margin:0;text-align:center}
.plate img{max-width:100%;height:auto}
.plate figcaption{font-family:Fell,Georgia,serif;font-style:italic;font-size:12.5px;
color:var(--faint);margin-top:5px}
/* *** SET INTO THE TEXT BLOCK, NOT ABOVE IT. ***
   A cut in its own row reserved 170px of height for a one-line sentence. Floated inside the
   paragraph it costs the height of the line it sits on, and the sentence runs around it the way
   it does in the books these came out of. */
/* *** SET LIKE A DROP INITIAL, NOT STACKED ABOVE THE TEXT. ***
   A cut in its own row reserved 170px of height for a one-line sentence, and shrinking it to fit
   the line made it a speck. Floated at the head of the paragraph it is legible AND the sentence
   runs around it, so the header costs the height of the cut and nothing more -- which is how a
   plate is set into a page in the books these came out of. */
.tabhead{margin:0 0 12px}
.tabcut{float:right;height:196px;width:auto;margin:0 0 16px 34px;mix-blend-mode:multiply}
.clearcut{clear:both}

/* ---- controls. A search box is a ruled line, not a pill. */
input[type=search],select,input[type=text]{font:inherit;font-size:14px;padding:5px 2px;
border:0;border-bottom:1px solid var(--rule);background:none;color:var(--ink);min-width:160px;
font-family:inherit}
input[type=search]{min-width:250px}
input[type=search]:focus,select:focus{outline:none;border-bottom-color:var(--ink)}
select{font-family:Fell,Georgia,serif;font-size:14px;cursor:pointer}
.bar{display:flex;gap:16px;align-items:baseline;margin-bottom:10px;flex-wrap:wrap}
.count{color:var(--faint);font-size:12.5px;font-family:Fell,Georgia,serif}
.big{font-size:24px;font-family:Fell,Georgia,serif}

/* ---- tables. Hairlines, no fill, numbers in old-style figures where the face has them. */
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{text-align:left;font-family:Fell,Georgia,serif;font-weight:400;font-size:12px;
text-transform:uppercase;letter-spacing:.07em;color:var(--faint);padding:5px 8px 4px;
border-bottom:1px solid var(--ink);position:sticky;top:0;background:var(--paper);cursor:pointer;
vertical-align:bottom}
th:hover{color:var(--ink)}
td{padding:6px 8px;border-bottom:1px solid var(--rule2);vertical-align:top}
tr.pick{cursor:pointer}
tr.pick:hover td{background:#f2efe7}
tr.on td{background:#efe9dc;box-shadow:inset 3px 0 0 var(--ink)}
.n{text-align:right;font-variant-numeric:tabular-nums}
/* *** WRAPPED, NEVER CUT. *** A cell used to end in an ellipsis at 300px, which hid the part of a
   claim that said what it was about. It wraps now, and the row is as tall as what it holds. */
td.clip{overflow-wrap:break-word;min-width:10em}
td{overflow-wrap:break-word}
/* Code has runs with no break in them (`home|deck|fence|pool|...`), and `break-word` does not
   lower a table column's minimum width, so a code cell may break anywhere as a last resort. */
td.mono{overflow-wrap:anywhere}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}

/* ---- the two-pane shell. Panes are separated by a rule, not by two boxes. */
.wrap2{display:grid;grid-template-columns:minmax(300px,1fr) minmax(0,1.55fr);gap:0;
align-items:stretch;height:100%}
.wrap2.wide{grid-template-columns:minmax(400px,1.1fr) minmax(0,1.3fr)}
.wrap2 > *{min-height:0}
.drillhost{display:flex;flex-direction:column;height:100%;min-height:0;gap:8px}
.drilltop{flex:0 0 auto}
.drilltop .note{margin:0}
.drillhost > .wrap2,.drillhost > .wrap3{flex:1 1 auto;min-height:0}
/* ---- three columns: every group, the picked group's rows, the picked row. */
.wrap3{display:grid;grid-template-columns:minmax(190px,250px) minmax(340px,1fr) minmax(0,1.2fr);
gap:0;align-items:stretch;height:100%}
.wrap3 > *{min-height:0}
.wrap3 > .pane{padding-left:16px}
.gnav{display:flex;flex-direction:column;height:100%;min-height:0;padding-right:12px;
border-right:1px solid var(--rule)}
.ghead{flex:0 0 auto;margin-bottom:6px}
.ghead input[type=search]{min-width:0;width:100%}
.gsorts{display:flex;gap:10px;align-items:baseline;margin-top:5px}
.gsort{appearance:none;border:0;background:none;padding:0;font:inherit;font-size:13px;
color:var(--ash);cursor:pointer;border-bottom:1px solid transparent}
.gsort:hover{color:var(--ink)}
.gsort.on{color:var(--ink);border-bottom-color:var(--ink)}
.glist{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden}
.gitem{appearance:none;border:0;background:none;display:grid;grid-template-columns:minmax(0,1fr) auto;
gap:1px 10px;width:100%;text-align:left;padding:5px 6px 5px 8px;font:inherit;font-size:13.5px;
line-height:1.35;color:var(--ink);cursor:pointer;border-bottom:1px solid var(--rule2)}
.gitem:hover{background:#f2efe7}
.gitem.on{background:#efe9dc;box-shadow:inset 3px 0 0 var(--ink)}
.gitem.zero{color:var(--faint)}
.gname{overflow-wrap:break-word;min-width:0}
.gn{font-variant-numeric:tabular-nums;color:var(--ash);font-size:12.5px}
.gsub{grid-column:1 / -1;font-size:12px;color:var(--faint)}
.gsect{font-family:Fell,Georgia,serif;font-size:12px;text-transform:uppercase;letter-spacing:.08em;
color:var(--faint);padding:12px 8px 3px;border-bottom:1px solid var(--rule)}
.pager{display:inline-flex;gap:8px}
.pager[hidden]{display:none}
.pg{appearance:none;border:1px solid var(--rule);background:none;font:inherit;font-size:13px;
color:var(--ink);padding:1px 9px;cursor:pointer}
.pg:hover:not(:disabled){border-color:var(--ink)}
.pg:disabled{color:var(--faint);cursor:default}
.facets{margin:4px 0 10px}
.fgrid{display:grid;grid-template-columns:minmax(0,max-content) minmax(60px,1fr) auto;gap:2px 12px;
max-height:190px;overflow-y:auto;align-items:center}
.frow{display:contents;cursor:pointer;font:inherit;color:inherit}
.frow > span{padding:2px 0;cursor:pointer;text-align:left}
.frow > .rval{text-align:right}
.frow:hover .flab{color:var(--rust)}
.frow.on .flab{color:var(--ink);text-decoration:underline}
.flab{font-size:13px;color:var(--ink);overflow-wrap:break-word}
.gridhost{display:flex;flex-direction:column;height:100%;min-height:0}
.gridhost > .bar{flex:0 0 auto}
.gridhost > .list{flex:1 1 auto}
.pane{display:flex;flex-direction:column;height:100%;min-height:0;padding-right:22px;
border-right:1px solid var(--rule)}
/* The group's header -- its question, its breakdown -- never takes the rows' room: on a short
   window it scrolls on its own, and the rows keep the rest. */
.pane > .panehead{flex:0 0 auto;max-height:42%;overflow-y:auto}
.pane > .gridhost{flex:1 1 auto;min-height:0}
.panebody{flex:1 1 auto;min-height:0;display:flex}
.panebody > .gridhost{flex:1 1 auto;min-height:0;width:100%}
.list{height:100%;min-height:0;overflow:auto}
.detail{padding:2px 4px 20px 22px;height:100%;overflow:auto}
.detail h2{margin:0 0 2px;font-family:Fell,Georgia,serif;font-weight:400;font-size:22px;
letter-spacing:.01em}
.detail h3{margin:20px 0 6px;font-family:Fell,Georgia,serif;font-size:12px;text-transform:uppercase;
letter-spacing:.08em;color:var(--faint);font-weight:400;border-bottom:1px solid var(--rule2);
padding-bottom:3px}
/* A filter or a column name as a heading keeps its own face: set in the display serif, `''`
   became a curly quote and read as a different filter. */
.detail h2.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:17px;
overflow-wrap:break-word}
.detail .path{color:var(--ash);font-size:12px;margin-bottom:12px;font-family:ui-monospace,
SFMono-Regular,Menlo,monospace}
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 18px;font-size:13.5px}
.kv dt{color:var(--faint);font-family:Fell,Georgia,serif}
.kv dd{margin:0}

/* ---- a pill is a lettered tag, the way a part is lettered on a plate. */
.pill{display:inline-block;font-family:Fell,Georgia,serif;font-size:11.5px;padding:0 6px;
border:1px solid var(--rule);color:var(--ash);white-space:nowrap;letter-spacing:.03em}
/* In a table cell a pill wraps like the text beside it, or it pushes its column off the pane. */
td .pill{white-space:normal}
.pill.declared,.pill.on{color:var(--iron);border-color:var(--iron)}
.pill.derived,.pill.observed{color:var(--ash)}
.pill.judged{color:var(--ember);border-color:var(--ember)}
.pill.bad{color:var(--rust);border-color:var(--rust)}
.note{color:var(--ash);font-size:13.5px;margin:8px 0 0;max-width:none}
.empty{color:var(--faint);padding:12px 4px;font-size:13.5px;font-style:italic;
font-family:Fell,Georgia,serif}
.prose{white-space:pre-wrap;font-size:14px;color:var(--ink);margin:0}
.tot{color:var(--faint)}
.bad{color:var(--rust)}
.ok{color:var(--iron)}
.low{color:var(--ember)}
a.lk{color:var(--rust);text-decoration:none;border-bottom:1px solid #e0c4b0}
a.lk:hover{border-bottom-color:var(--rust)}
details{margin:5px 0}
summary{cursor:pointer;color:var(--ash);font-size:13px;font-family:Fell,Georgia,serif}
pre{background:#f4f1e9;border:0;border-left:2px solid var(--rule);padding:8px 12px;
overflow:auto;font-size:12px;margin:6px 0;white-space:pre-wrap;word-break:break-word}
.quote{margin:4px 0 8px;padding-left:13px;border-left:2px solid var(--ink);color:var(--ink);
font-size:14.5px;font-style:italic}

/* ---- the assay ticket: the overview's four columns, ruled like an account. */
.tlead{font-family:Fell,Georgia,serif;font-size:21px;line-height:1.35;margin:0 0 16px}
.ticket{display:grid;grid-template-columns:repeat(4,1fr);gap:0;margin:0;
border-bottom:1px solid var(--ink)}
.qhead{padding:10px 0 4px;margin-top:8px;border-top:1px solid var(--rule2)}
.qhead .quote{margin:4px 0 6px}
.tcol{padding:2px 22px 16px 0}
.tcol + .tcol{padding-left:22px;border-left:1px solid var(--rule)}
@media (max-width:900px){.ticket{grid-template-columns:repeat(2,1fr)}
.tcol:nth-child(3){padding-left:0;border-left:0}}
.tlab{font-family:Fell,Georgia,serif;font-size:12px;text-transform:uppercase;letter-spacing:.11em;
color:var(--faint);margin-bottom:6px}
.tnum{font-family:Fell,Georgia,serif;font-size:50px;line-height:.95;letter-spacing:-.01em}
.tunit{font-family:Fell,Georgia,serif;font-size:17px;color:var(--ash);margin-left:7px}
.tsub{font-size:13px;color:var(--ash);margin-top:9px;padding-top:8px;
border-top:1px solid var(--rule2)}
.ticketwrap{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:30px;align-items:center;
margin-bottom:22px}
.ticketcut{width:215px;height:auto;display:block;mix-blend-mode:multiply}

/* ---- a section in the overview */
.ovblock{margin:0 0 28px}
.ovblock h3{margin:0 0 4px;font-family:Fell,Georgia,serif;font-size:13px;text-transform:uppercase;
letter-spacing:.1em;color:var(--ink);font-weight:400;border-bottom:1px solid var(--rule);
padding-bottom:4px}
.ovblock .note{margin:0 0 12px}
.hero{display:flex;gap:26px;flex-wrap:wrap;margin:4px 0 22px}
.herobig{flex:1 1 260px;min-width:240px}
.herobig .lab{font-family:Fell,Georgia,serif;font-size:12px;text-transform:uppercase;
letter-spacing:.1em;color:var(--faint);margin-bottom:4px}
.heron{font-family:Fell,Georgia,serif;font-size:46px;line-height:1}
.heron.small{font-size:34px}
.heroof{font-size:17px;color:var(--ash);font-family:Fell,Georgia,serif}

/* ---- tiles become ruled entries in a column, not boxes */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0;margin:0 0 22px;
border-top:1px solid var(--rule)}
.tile{padding:10px 18px 12px 0;border-bottom:1px solid var(--rule2)}
.tile + .tile{padding-left:18px;border-left:1px solid var(--rule2)}
.tilebig{font-family:Fell,Georgia,serif;font-size:26px;line-height:1.05}
.tilebig.bad{color:var(--rust)}
.tilelab{font-size:13px;color:var(--ink);margin-top:3px}
.tilenote{font-size:12px;color:var(--faint);margin-top:2px}

/* ---- bars. The only colour on the page. */
.sbar{display:flex;gap:1px;height:22px;width:100%}
.sseg{display:flex;align-items:center;overflow:hidden;min-width:2px}
.sval{font:400 12px Fell,Georgia,serif;color:var(--paper);padding-left:8px;white-space:nowrap}
.rank{display:grid;grid-template-columns:auto minmax(80px,1fr) auto;gap:4px 12px;
align-items:center}
.rrow{display:contents}
.rrow.clk{cursor:pointer}
.rlab{font-size:12.5px;color:var(--ink);white-space:nowrap;text-align:right;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.rsub{display:block;font-size:11px;color:var(--faint);font-family:Fell,Georgia,serif}
.rtrack{height:12px;display:block;border-bottom:1px solid var(--rule2)}
.rfill{display:block;height:12px}
.rrow.clk:hover .rfill{opacity:.8}
.rval{font-size:12.5px;color:var(--ash);white-space:nowrap;font-variant-numeric:tabular-nums}
.days{display:flex;align-items:flex-end;gap:3px;height:92px;margin:6px 0 14px;overflow-x:auto}
.day{display:flex;flex-direction:column;justify-content:flex-end;align-items:center;
min-width:22px;flex:1 1 22px;height:100%}
.daytrack{width:100%;height:68px;display:flex;align-items:flex-end;
border-bottom:1px solid var(--rule)}
.dayfill{width:100%;background:var(--rust)}
.day.zero .dayfill{background:var(--rule)}
.daylab{font-size:10px;color:var(--faint);margin-top:4px;white-space:nowrap;
font-family:Fell,Georgia,serif}
.srclab{font-size:13px;color:var(--ash);margin:14px 0 5px;font-family:Fell,Georgia,serif;
font-style:italic}
.srclab:first-child{margin-top:0}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:12.5px;color:var(--ash)}
/* A monitoring finding is a name and a sentence, set as two columns so the names line up
   and the sentences read as a list rather than as a paragraph each. */
.mfrow{display:flex;gap:16px;align-items:baseline;padding:7px 0;
border-bottom:1px solid var(--rule2)}
.mfrow .rlab{flex:0 0 250px;text-align:left}
.lgi{display:inline-flex;gap:6px;align-items:center}
.sw{width:11px;height:11px;display:inline-block}

/* ---- buttons read as printed catchwords */
button.back{appearance:none;border:0;border-bottom:1px solid var(--rule);background:none;
font-family:Fell,Georgia,serif;font-size:14px;color:var(--rust);padding:2px 0;cursor:pointer;
margin:0 14px 8px 0}
button.back:hover{border-bottom-color:var(--rust)}
button.back.on{color:var(--ink);border-bottom-color:var(--ink)}
.titlerow{display:flex;align-items:baseline;gap:14px;margin:6px 0 8px}
.titlerow .back{margin:0}
h3.crumb{margin:0;font-family:Fell,Georgia,serif;font-size:16px;font-weight:400;color:var(--ink);
text-transform:none;letter-spacing:.01em;border:0;padding:0}
.crumb{color:var(--ash);font-size:14px}
.chips{display:flex;gap:0;flex-wrap:wrap;margin:0 0 10px;align-items:baseline}
.chips.flat{margin:6px 0 0;gap:6px}
.chip{appearance:none;font-family:Fell,Georgia,serif;font-size:13.5px;border:0;
border-bottom:2px solid transparent;background:none;padding:2px 11px 2px 0;cursor:pointer;
display:inline-flex;gap:6px;align-items:baseline;color:var(--ash);margin-right:5px}
.chip:hover{color:var(--ink)}
.chip.on{color:var(--ink);border-bottom-color:var(--ink)}
.chip b{font-weight:400;color:var(--faint);font-size:12px}
.chips.flat .chip{cursor:default;border:1px solid var(--rule);padding:0 6px;margin:0;
max-width:100%;overflow-wrap:anywhere;white-space:normal}
.chips .more{margin:0 14px 0 4px}
label.chk{display:inline-flex;gap:6px;align-items:center;font-size:13px;color:var(--ash);
cursor:pointer;user-select:none;white-space:nowrap;font-family:Fell,Georgia,serif}
label.chk:hover{color:var(--ink)}
label.chk input{margin:0;cursor:pointer;accent-color:var(--ink)}

/* ---- suggestions */
.sug{border:0;border-left:2px solid var(--ink);padding:8px 14px;margin:0 0 10px}
.sug-h{margin-bottom:5px}
.sug-m{margin:0 0 6px;padding-left:18px;color:var(--ash);font-size:13px}
.sug-m li{margin:1px 0}
.sug-d{white-space:pre-wrap;font-size:13px;border-left:2px solid var(--ember);
padding:5px 0 5px 11px;margin:6px 0;color:var(--ink)}
.sug-y{white-space:pre-wrap;font-size:12px;background:#f4f1e9;border:0;
border-left:2px solid var(--rule);padding:8px 12px;margin:6px 0 0;overflow-x:auto}

/* ---- rendered markdown */
.md > :first-child{margin-top:0}
.md > :last-child{margin-bottom:0}
.mdh{margin:14px 0 5px;font-family:Fell,Georgia,serif;font-size:15px;font-weight:400}
.mdlist{margin:6px 0;padding-left:20px;font-size:13.5px;color:var(--ink)}
.mdlist li{margin:2px 0}
.mdpre{white-space:pre-wrap;font-size:12px;background:#f4f1e9;border:0;
border-left:2px solid var(--rule);padding:8px 12px;margin:8px 0;overflow-x:auto}
.md code{font-size:12px;background:#f4f1e9;padding:0 3px}

/* ---- the empty-store notice: a printed errata slip */
.newstore{border-top:1px solid var(--ink);border-bottom:1px solid var(--ink);
padding:11px 0;margin:0 0 20px;font-size:13.5px;color:var(--ink)}
.newstore b{font-family:Fell,Georgia,serif;font-weight:400}

/* ---- the lineage drawing */
.linwrap{overflow:hidden;border:1px solid var(--rule);padding:0;position:relative;
height:var(--lin-h);touch-action:none}
.linwrap svg.lin{width:100%;height:100%;display:block;cursor:grab}
.linwrap svg.lin.drag{cursor:grabbing}
.lintools{position:absolute;right:8px;top:8px;display:flex;gap:6px;z-index:2}
.lintools button{font-family:Fell,Georgia,serif;font-size:14px;line-height:1;padding:4px 8px;
background:var(--paper);border:1px solid var(--rule);color:var(--ash);cursor:pointer}
.lintools button:hover{color:var(--ink);border-color:var(--ink)}
.linhint{position:absolute;left:10px;bottom:8px;font-size:12px;color:var(--faint);z-index:2;
pointer-events:none;font-family:Fell,Georgia,serif;font-style:italic}
svg.lin{display:block}
svg.lin rect{fill:var(--paper);stroke:var(--ink);stroke-width:1.4}
svg.lin .foc rect{fill:#efe9dc;stroke:var(--ink);stroke-width:2.6}
svg.lin .bt{font:400 13px Fell,Georgia,serif;fill:var(--ink)}
svg.lin .bs{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--ash)}
svg.lin .ln{fill:none;stroke:var(--iron);stroke-width:1.2}
svg.lin .ln.drv{stroke:var(--ink);stroke-width:2.2}
svg.lin .ln.nb{stroke:var(--rust);stroke-width:2;stroke-dasharray:5 3}
svg.lin .par.nb rect{stroke:var(--rust)}
svg.lin marker path{fill:var(--iron)}
svg.lin .clk{cursor:pointer}
svg.lin .clk:hover rect{stroke:var(--rust);stroke-width:2.2}

/* ---- the node card */
.pop{position:fixed;z-index:50;width:370px;max-width:calc(100vw - 24px);max-height:70vh;
overflow:auto;background:var(--paper);border:1px solid var(--ink);
box-shadow:3px 3px 0 rgba(26,23,20,.14);padding:13px 15px 11px}
.pop .popname{font-family:Fell,Georgia,serif;font-size:16px;padding-right:18px}
.pop .path{font-size:11.5px;margin:2px 0 6px}
.pop .prose{font-size:13px}
.pop .kv{margin-top:8px;font-size:13px}
.pop h3{margin:12px 0 4px}
.pop .bar{margin:10px 0 0}
.popx{position:absolute;top:5px;right:9px;appearance:none;border:none;background:none;
font-size:18px;line-height:1;color:var(--faint);cursor:pointer;padding:0 2px}
.popx:hover{color:var(--ink)}
.bandlist{margin:8px 0}
.opt{border:0;border-left:1px solid var(--rule);padding:6px 0 6px 12px;margin:8px 0}
.optname{font-family:Fell,Georgia,serif;font-size:14px;margin-bottom:3px}
.kv.sub{margin:2px 0 6px 0;padding-left:12px;border-left:1px solid var(--rule2)}
.sub{margin:3px 0}
details.strip{margin:0 0 14px;border:0;border-top:1px solid var(--rule);
border-bottom:1px solid var(--rule);padding:8px 0}
details.strip summary{color:var(--ink);font-size:13.5px}

footer{color:var(--faint);font-size:12px;padding:14px 26px;border-top:3px double var(--ink);
font-family:Fell,Georgia,serif;font-style:italic}
footer b{font-style:normal;font-weight:400;color:var(--ash)}
"""

JS = r"""
const $ = (s, r) => (r || document).querySelector(s);
const el = (t, a, kids) => { const n = document.createElement(t);
  for (const k in (a || {})) { if (k === 'text') n.textContent = a[k];
    else if (k === 'html') n.innerHTML = a[k]; else if (a[k] != null) n.setAttribute(k, a[k]); }
  for (const c of (kids || [])) n.append(c); return n; };
const num = n => (n == null ? '' : Number(n).toLocaleString('en-US'));
/* Bytes a person reads. `null` is "not estimated" and never a zero: a zero would read as "this
   query scanned nothing", which is a claim, and an absent estimate is not one. */
const bytes = n => {
  if (n == null) return 'not estimated';
  let v = Number(n);
  for (const u of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (v < 1024 || u === 'TB') return (u === 'B' ? v.toFixed(0) : v.toFixed(1)) + ' ' + u;
    v /= 1024;
  }
  return v.toFixed(1) + ' TB';
};
/* *** 45806589 IS NOT A NUMBER ANYBODY READS. ***
   The prose on this page has always grouped its thousands and the TABLES never did, so a token
   count, a row count and a model count all arrived as a run of digits you have to count with a
   finger. Whole numbers only: a confidence is 0.87 and a weight is 12.5, and grouping those would
   round them -- `toLocaleString` caps at three fraction digits by default, which would quietly
   change a displayed probability. */
const cellText = v => (v == null ? ''
  : (typeof v === 'number' && Number.isInteger(v) ? num(v) : String(v)));
/* *** A LONG NAME BREAKS AT ITS OWN SEPARATORS, NOT MID-WORD AND NOT OFF THE EDGE. ***
   `int_water_county_referral_rollup` in a table cell either pushed the table sideways into a
   scrollbar or was cut with an ellipsis, and both were reported: "side scroll in this table isnt
   something i really want", "not very helpful when all the shit is just like cut off". A `<wbr>`
   after each `_ . / ,` lets it wrap where a person would break it, and `<wbr>` is not text, so
   copying the name or comparing `textContent` still gets the name. */
function wbr(s) {
  const f = document.createDocumentFragment();
  String(s == null ? '' : s).split(/(?<=[_./,|])/).forEach((p, i) => {
    if (i) f.append(document.createElement('wbr'));
    f.append(document.createTextNode(p));
  });
  return f;
}
const pct = x => (x == null ? '' : Math.round(x * 100) + '%');

/* A Fact with its provenance. A value with no source is a rumour, so the pill is never dropped. */
function fact(f) {
  if (!f) return el('span', {class: 'tot', text: 'not settled'});
  const v = Array.isArray(f.value) ? f.value.join(', ') : String(f.value);
  const s = el('span', {});
  s.append(el('span', {text: v + ' '}));
  s.append(el('span', {class: 'pill ' + (f.source || ''),
    text: f.source + (f.confidence != null ? ' @' + f.confidence.toFixed(2) : '')}));
  if (f.resting_on) s.append(el('span', {class: 'pill bad', text: 'rests on a premise'}));
  return s;
}

/* One sortable, filterable table. Sorting is a VIEW, never a mutation of DATA: the embedded
   arrives sorted from the assembly layer and that order is what the file's determinism rests on. */
function grid(rows, cols, opts) {
  opts = opts || {};
  let sort = opts.sort || null, dir = opts.dir || 1, q = '', pg = 0;
  const search = el('input', {type: 'search', placeholder: opts.placeholder || 'filter...'});
  const count = el('span', {class: 'count'});
  const prev = el('button', {class: 'pg', text: '\u2039 prev'});
  const next = el('button', {class: 'pg', text: 'next \u203a'});
  prev.onclick = () => { pg = Math.max(0, pg - 1); draw(); listBox.scrollTop = 0; };
  next.onclick = () => { pg++; draw(); listBox.scrollTop = 0; };
  const pager = el('span', {class: 'pager'}, [prev, next]);
  const bar = el('div', {class: 'bar'}, opts.page ? [search, count, pager] : [search, count]);
  for (const extra of (opts.controls || [])) bar.append(extra);
  const head = el('tr', {}, cols.map(c => {
    const th = el('th', {text: c.label, class: c.n ? 'n' : ''});
    th.onclick = () => { if (sort === c.key) dir = -dir; else { sort = c.key; dir = 1; } pg = 0; draw(); };
    return th;
  }));
  const body = el('tbody');
  const table = el('table', {}, [el('thead', {}, [head]), body]);
  /* A scrolling grid is a flex column: the bar is what it needs, the list is everything left.
     Without this the bar sits OUTSIDE the height budget and the column overflows its pane. */
  const listBox = el('div', {class: opts.scroll ? 'list' : ''}, [table]);
  const host = el('div', {class: opts.scroll ? 'gridhost' : ''}, [bar, listBox]);

  function draw() {
    let view = rows;
    if (q) { const t = q.toLowerCase();
      view = rows.filter(r => (opts.text ? opts.text(r) : JSON.stringify(r)).toLowerCase().includes(t)); }
    if (opts.where) view = view.filter(opts.where);
    if (sort) { const c = cols.find(x => x.key === sort);
      view = view.slice().sort((a, b) => {
        const x = c.val(a), y = c.val(b);
        if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
        return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * dir;
      }); }
    /* *** A PAGE, NOT A CAP. ***
       A cap with no notice reads as "that is all of them", and a cap WITH a notice still leaves
       rows 4,001 to 5,820 unreachable. A paged table reaches every row, and 200 wrapped rows
       draw at once where 4,000 did not. Without `page` the old cap stands, and says so. */
    let shown;
    if (opts.page) {
      const pages = Math.max(1, Math.ceil(view.length / opts.page));
      if (pg >= pages) pg = pages - 1;
      shown = view.slice(pg * opts.page, (pg + 1) * opts.page);
      const from = view.length ? pg * opts.page + 1 : 0, to = pg * opts.page + shown.length;
      count.textContent = num(from) + '\u2013' + num(to) + ' of ' + num(view.length)
        + (view.length === rows.length ? '' : ' (' + num(rows.length) + ' before the filter)');
      prev.disabled = pg === 0; next.disabled = pg >= pages - 1;
      pager.hidden = pages < 2;
    } else {
      const cap = opts.cap || 1200;
      shown = view.slice(0, cap);
      count.textContent = view.length === rows.length
        ? num(rows.length) + ' row(s)' + (view.length > cap ? ', showing ' + num(cap) : '')
        : num(view.length) + ' of ' + num(rows.length) + (view.length > cap ? ', showing ' + num(cap) : '');
    }
    body.replaceChildren(...shown.map(r => {
      const tr = el('tr', {class: opts.pick ? 'pick' : ''},
        cols.map(c => el('td', {class: (c.n ? 'n ' : '') + (c.mono ? 'mono ' : '')
                                       + (c.clip ? 'clip' : '')},
          [c.cell ? c.cell(r) : typeof c.val(r) === 'number'
            ? el('span', {text: cellText(c.val(r))})
            : el('span', {}, [wbr(cellText(c.val(r)))])])));
      if (opts.pick) tr.onclick = () => { body.querySelectorAll('tr.on').forEach(x => x.classList.remove('on'));
        tr.classList.add('on'); opts.pick(r); };
      return tr;
    }));
    if (!shown.length) body.replaceChildren(el('tr', {}, [el('td', {class: 'empty',
      colspan: cols.length, text: opts.emptyText || 'nothing matches'})]));
  }
  search.oninput = () => { q = search.value; pg = 0; draw(); };
  draw();
  host.redraw = () => { pg = 0; draw(); };
  return host;
}

/* *** SOME OF THESE PANES ARE DOCUMENTS, NOT INTERFACES. ***
   A model's description and a finding's detail are prose with backticks, blank-line paragraphs
   and the occasional list, written by people who write Markdown -- and they were rendered as one
   `<p>` with the asterisks and hashes still in it. "all the attempted markdown that doesnt
   render."

   Sixty lines, inline, no dependency: the page stays one file that opens off a disk with no
   network. It builds NODES and never HTML, so a description containing a script tag is a
   description containing a script tag. A link is rendered as a link only when it is http(s);
   anything else stays text, because `javascript:` in somebody's schema.yml is not a link. */
function mdInline(text) {
  const out = [];
  const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[[^\]\n]+\]\([^)\s]+\))/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(document.createTextNode(text.slice(last, m.index)));
    const s = m[0];
    if (s[0] === '`') out.push(el('code', {text: s.slice(1, -1)}));
    else if (s.slice(0, 2) === '**') out.push(el('b', {text: s.slice(2, -2)}));
    else if (s[0] === '*') out.push(el('i', {text: s.slice(1, -1)}));
    else {
      const cut = s.indexOf(']');
      const label = s.slice(1, cut), href = s.slice(cut + 2, -1);
      out.push(/^https?:\/\//.test(href)
        ? el('a', {class: 'lk', href: href, target: '_blank', rel: 'noreferrer', text: label})
        : document.createTextNode(label));
    }
    last = m.index + s.length;
  }
  if (last < text.length) out.push(document.createTextNode(text.slice(last)));
  return out;
}

function md(text) {
  const host = el('div', {class: 'md'});
  const lines = String(text == null ? '' : text).split('\n');
  let i = 0, list = null, listTag = null;
  const endList = () => { list = null; listTag = null; };
  while (i < lines.length) {
    const ln = lines[i];
    if (/^\s*```/.test(ln)) {
      endList();
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;                                       // the closing fence, or the end of the text
      host.append(el('pre', {class: 'mdpre', text: buf.join('\n')}));
      continue;
    }
    if (/^\s*$/.test(ln)) { endList(); i++; continue; }
    const h = ln.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      endList();
      /* h4 whatever the depth: these sit INSIDE a pane that already has an h2 and an h3, and a
         description opening with `# Overview` must not outrank the thing it describes. */
      host.append(el('h4', {class: 'mdh'}, mdInline(h[2])));
      i++;
      continue;
    }
    const bullet = ln.match(/^\s*[-*+]\s+(.*)$/);
    const number = ln.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || number) {
      const want = bullet ? 'ul' : 'ol';
      if (listTag !== want) { endList(); list = el(want, {class: 'mdlist'}); host.append(list); }
      listTag = want;
      list.append(el('li', {}, mdInline((bullet || number)[1])));
      i++;
      continue;
    }
    endList();
    /* A paragraph is consecutive prose lines joined, so a description hard-wrapped at 100 columns
       reads as a paragraph rather than as eight short ones. */
    const para = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^\s*```/.test(lines[i])
           && !/^#{1,6}\s/.test(lines[i]) && !/^\s*[-*+]\s+/.test(lines[i])
           && !/^\s*\d+[.)]\s+/.test(lines[i])) {
      para.push(lines[i].trim());
      i++;
    }
    if (para.length) host.append(el('p', {class: 'prose'}, mdInline(para.join(' '))));
  }
  return host;
}

/* *** A PLATE PER TAB, BESIDE WHAT THE TAB IS FOR. ***
   The cuts are apparatus: a rank of stills, a chain of vessels, a furnace in draught. Each tab
   takes the one whose picture is what the tab does, floated beside its opening line, so the page
   reads as a plate book rather than as a table with a picture on the front. */
function section(title, node) {
  const h = el('h3', {text: title});
  return el('div', {}, node ? [h, node] : [h]);
}

function kv(pairs) {
  const d = el('dl', {class: 'kv'});
  for (const [k, v] of pairs) { if (v == null) continue;
    d.append(el('dt', {text: k})); d.append(el('dd', {}, [typeof v === 'string' ? el('span', {text: v}) : v])); }
  return d;
}
"""


def build_fingerprint() -> str:
    """A short hash of the code that renders this page, so the version stamp is EVIDENCE.

    *** THE STAMP WAS AN ASSERTION THE PAGE MADE ABOUT ITSELF, AND IT WAS FALSE. ***
    Reported from the field: `uvx --from dbt-assay==0.47.2` served a cached 0.47.1 environment
    while the process reported itself as 0.47.2, and the page it produced was completely dead.
    The published wheel was correct. It took reading `explorer.py` out of the wheel inside the
    container to settle it, because every number on the page agreed with every other number and
    all of them came from the same wrong install.

    A version is what the package SAYS it is. This is what the rendering code actually IS, so two
    pages claiming one version and differing here came from two different installs -- the same
    argument `state_hash` already makes for a judged answer.
    """
    h = hashlib.sha256()
    for part in (CSS, JS, _VIEWS):
        h.update(part.encode("utf-8"))
    return h.hexdigest()[:12]


def explorer_html(data: dict, record_html: str) -> str:
    """The whole thing: one file, embedded data, tabs.

    `record_html` is the existing report, folded in as a tab rather than rewritten. It is the one
    surface with an argument to make rather than a table to show, and it keeps making it.
    """
    e = html.escape
    meta = data["meta"]

    # *** `</script>` INSIDE THE DATA WOULD END THE TAG AND SILENTLY TRUNCATE THE FILE. ***
    # A dbt model that contains that string in a comment is not exotic. Escaping the sequence is
    # what stops a warehouse's own content from breaking the page that describes it. `<!--` too:
    # it opens a comment inside a script element in the HTML parser.
    # *** THE RECORD TRAVELS INSIDE THE BLOB, NOT AS MARKUP. ***
    # It is a whole document with its own <style>, so dropping it into a div would clobber this
    # page's styles with its own. It goes into an isolated iframe instead, and the only safe way
    # to carry a document through HTML is as a JSON string -- json encoding already escapes
    # everything, where hand-escaping a nested document is how a page silently truncates.
    # *** ONE SHAPE, WHICHEVER PATH GOT HERE. ***
    # `read_data` fills every declared section, so an artifact round-trip hands back `[]` where a
    # direct call from the store simply has no key. The page then embeds two different JSON blobs
    # for one project, and `assay page --from` stops reproducing the page it was made from. The
    # round-trip guard caught exactly this the first time a section was added, which is the whole
    # reason that guard compares bytes rather than rendering.
    from .assets import CUTS, FAVICON, FONT_CSS, MARK_SVG
    from .explore import _LINES, _WHOLE
    filled = {n: [] for n in _LINES}
    filled.update({n: empty() for n, empty in _WHOLE})
    # The plates travel with the data rather than being spelled into the markup, so a view asks
    # for `cuts.furnace` the way it asks for anything else it draws.
    data = {**filled, **data, "record": record_html, "cuts": CUTS}
    blob = json.dumps(data, separators=(",", ":"), sort_keys=True, default=str)
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\!--")

    counts = {
        "models": len(data["models"]), "edges": len(data["edges"]),
        "claims": len(data["claims"]), "findings": len(data["findings"]),
        "decisions": len(data["decisions"]), "questions": len(data["questions"]),
        "unreadable": len(data["unreadable"]),
        "suggestions": len(data.get("suggestions") or []),
        # The count is the findings ABOUT the monitoring, which is the number worth a badge.
        # None when nothing was measured, so the tab shows no count rather than a zero.
        "monitoring": (len((data.get("monitoring") or {}).get("monitoring") or [])
                       if (data.get("monitoring") or {}) else None) or None,
        # all three lists, not the first one: the tab said 16 while it held 16 + 39 + the odd ones
        "areas": sum(len((data.get("areas") or {}).get(k) or [])
                     for k in ("predicate_clusters", "odd_ones_out", "same_claim")) or None,
    }
    # *** THE OVERVIEW IS THE WAY IN, NOT THE LAST TAB. ***
    # It is the only surface here with an argument to make rather than a table to show, and a
    # person opening this file has not yet picked a model to look at. Landing on 358 rows asks
    # them to choose before they have been told anything.
    tabs = [
        ("understood", "Overview", None),
        ("models", "Models", counts["models"]),
        ("chain", "The chain", counts["edges"]),
        ("claims", "Claims", counts["claims"]),
        ("findings", "Findings", counts["findings"]),
        ("areas", "Areas", counts["areas"]),
        # *** IS ANYTHING WATCHING THIS, AND ARE THE TESTS ACTUALLY RUNNING. ***
        # Present whether or not the numbers were taken: a tab that appears only when somebody
        # passed `--monitoring` is a tab nobody learns exists, and its absence reads as a tool
        # that does not do this rather than as a measurement not yet made.
        ("monitoring", "Monitoring", counts["monitoring"]),
        ("suggest", "What to configure", counts["suggestions"]),
        ("answers", "Answers", counts["decisions"]),
        ("spend", "Spend", None),
        ("questions", "Questions", counts["questions"]),
        ("config", "Config", None),
    ]
    # *** THIS PAGE IS READ-ONLY AND THE FORM IS WHERE YOU CHANGE THINGS. ***
    # Two artifacts that link, rather than one that half-does both: the report says WHAT, the form
    # owns every box you type into. Without a link between them the split reads as a missing
    # feature -- "it doesnt even offer the ability to configure anything?" -- rather than as a
    # decision. A relative path, so the pair travels as two files in one directory.
    form = str(data.get("meta", {}).get("form") or "")
    form_link = (f' &middot; <a class="lk" href="{e(form)}">open the review form</a>'
                 if form else "")
    nav = "".join(
        f'<button role="tab" data-tab="{t}" aria-selected="{"true" if i == 0 else "false"}">'
        f'{e(label)}{f"<b>{n:,}</b>" if n is not None else ""}</button>'
        for i, (t, label, n) in enumerate(tabs))
    panels = "".join(f'<div class="panel" id="p-{t}"{"" if i == 0 else " hidden"}></div>'
                     for i, (t, _l, _n) in enumerate(tabs))

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(meta['project'])} &middot; assay</title>
<link rel="icon" href="{FAVICON}">
<style>{FONT_CSS}{CSS}</style></head><body>
<header>
<h1>{MARK_SVG}<span class="hname">{e(meta['project'])}</span><span>everything assay knows</span></h1>
<div class="sub">{meta['models']} models &middot; {meta['sources']} sources &middot;
manifest generated {e(str(meta['generated_at']))} &middot;
<span title="A hash of the code that rendered this page. The version is what the package says it is; this is what the rendering code actually IS, so two pages claiming one version and differing here came from two different installs.">assay {e(meta['version'])} &middot; build {e(build_fingerprint())}</span>{form_link}</div>
<nav role="tablist">{nav}</nav>
</header>
<main>{panels}</main>
<footer>
Self-contained and deterministic: it carries the manifest's own <b>generated_at</b> and never a
wall clock, so a rerun against an unchanged store writes an identical file. Every array is sorted
in the assembly layer, not in the browser, so the sort is a fact about the file rather than about
the machine that opened it.
</footer>

<!-- THE ONE SWAPPABLE LINE. Embedded here; a server would make this a fetch and nothing
     below would change. -->
<script id="assay-data" type="application/json">{blob}</script>
<script>
const DATA = JSON.parse(document.getElementById('assay-data').textContent);
{JS}
{_VIEWS}
</script>
</body></html>"""


# --------------------------------------------------------------------------------- the tab views

_VIEWS = r"""
const M = DATA.models, BY_UID = Object.fromEntries(M.map(m => [m.uid, m]));
const BY_NAME = Object.fromEntries(M.map(m => [m.name, m]));
const CLAIM = Object.fromEntries(DATA.claims.map(c => [c.id, c]));
const FIND = Object.fromEntries(DATA.findings.map(f => [f.id, f]));
const EDGES_IN = {}, EDGES_OUT = {};
for (const e of DATA.edges) {
  (EDGES_IN[e.child] = EDGES_IN[e.child] || []).push(e);
  (EDGES_OUT[e.parent] = EDGES_OUT[e.parent] || []).push(e);
}

/* *** THE TABS WERE EIGHT ISLANDS. *** A model name is the one thing every table has in common,
   so every one of them is a way back to that model. This is the cheapest thing on the page and
   the one that turns separate lists into somewhere you can actually move around. */
/* *** A MODEL FROM AN INSTALLED PACKAGE IS IN THE MANIFEST AND IS NOT YOURS. ***
   30 of 358 here, every one unreadable, carrying 541 columns of unknown provenance, all diluting
   numbers about the project somebody actually wrote. The split is by OWNER and never by whether
   the model parsed: a package's model failing to parse is not your problem, and one of YOURS
   failing to parse is the "you did not compile" signal -- which a filter on `unreadable` would
   have hidden, and which is the whole reason not to write that filter. */
/* *** AN EMPTY COLUMN ON EVERY ROW IS NOT A RESULT, IT IS A QUESTION NOBODY ASKED. ***
   Every one of 5,656 columns on the field warehouse had no role, because `assay columns` has
   never been run there. Printing "not settled" 5,656 times says the same thing as a check that
   found nothing, which is the failure this whole tool is built to name. So the column is dropped
   and the reason is said once, with the command that would fill it. */
const HAS_ROLES = M.some(m => (m.columns || []).some(c => c.role));
const PACKAGED = M.filter(m => !m.yours).length;
let showPackaged = false;
const PKG_BOXES = [];
function packageFilter(redraw) {
  if (!PACKAGED) return null;
  const cb = el('input', {type: 'checkbox'});
  cb.checked = showPackaged;
  cb.onchange = () => { showPackaged = cb.checked;
    for (const b of PKG_BOXES) { b.box.checked = showPackaged; b.redraw(); } };
  PKG_BOXES.push({box: cb, redraw: redraw});
  return el('label', {class: 'chk'}, [cb, el('span',
    {text: 'include ' + PACKAGED + ' model(s) from installed packages'})]);
}
const mine = m => showPackaged || m.yours;

const GO = {};
/* Mark the row for `name` as the selected one, and scroll it into view, WITHOUT filtering. If it
   is not in the current view -- filtered out, or past the cap -- nothing happens, which is
   correct: the detail already shows the model and the list is only an index into it. */
function highlight(panel, name) {
  const host = document.querySelector(panel); if (!host) return;
  host.querySelectorAll('tbody tr.on').forEach(r => r.classList.remove('on'));
  for (const tr of host.querySelectorAll('tbody tr')) {
    const first = tr.querySelector('td');
    if (first && first.textContent.trim() === name) {
      tr.classList.add('on');
      if (tr.scrollIntoView) tr.scrollIntoView({block: 'nearest'});
      return;
    }
  }
}
function link(name, where) {
  if (!BY_NAME[name]) return el('span', {class: 'mono'}, [wbr(name || '')]);
  const a = el('a', {class: 'mono lk', href: '#'}, [wbr(name)]);
  a.onclick = ev => { ev.preventDefault(); ev.stopPropagation();
    open(where || 'models'); (GO[where || 'models'] || (() => {}))(name); };
  return a;
}

/* A grouped front door. *** NO TAB OPENS ON A FLAT LIST OF EVERYTHING. ***
   5,794 claims in one scroll is not more information than 358 models in one scroll, it is less:
   the first screen tells you nothing about the shape of what is there and gives you nowhere
   obvious to click. So the groups are always on screen, and the rows are the picked group's. */
function drill(opts) {
  /* *** ONE CLICK. ALWAYS. THE RIGHT PANE IS NEVER EMPTY. ***
     This used to be three screens to read one thing: a table of groups, then a click to a table
     of rows, then a click to the content -- with the right-hand pane saying "pick something" at
     two of the three steps. Reported exactly as it deserved: "what in the fuck was your decision
     process when you decided this 3 step process was necessary to get any information."

     So the list in the middle is always the leaves -- the claims, the answers, the candidates,
     the things somebody came here to read. One click on a row fills the right pane, and the pane
     is filled on arrival with the first row.

     *** EVERY GROUP, NOT THE TOP EIGHT. ***
     The groups were a row of chips capped at eight, then "+343 more, use the filter": 343 models'
     claims and 19 question families of thousands of answers each could only be found by knowing
     their name first. Reported with a screenshot: "you cant even select some of em ... which is
     awful UI design". So the groups are their own column, every one of them, with its full name
     and count, a filter of their own, and a scroll. The chip labels were also cut at 29
     characters and set small in the display face, and both were called hard to read. */
  const host = el('div', {class: 'drillhost'});
  const top = el('div', {class: 'drilltop'});
  const cols = el('div', {class: 'wrap3'});
  const gnav = el('div', {class: 'gnav'});
  const left = el('div', {class: 'pane'});
  const detail = el('div', {class: 'detail'});
  const head = el('div', {class: 'panehead'});
  const body = el('div', {class: 'panebody'});

  const ALL = {__all: 1};
  const withAll = opts.all !== false;
  let pickedGroup = (opts.startGroup && opts.startGroup(opts.groups))
    || (withAll ? ALL : opts.groups[0]);
  /* *** A VIEW SWITCH IS A FILTER, SO IT SITS WITH THE OTHER FILTER. ***
     A toggle narrows the rows of whichever group is picked, so it is a checkbox in the rows' own
     filter bar, and the group counts on the left follow it. */
  const toggles = (opts.toggles || []).map(t => ({...t, on: false}));
  /* One value of the facet (an answer, a kind) picked inside the group. Reset when the group
     changes, because a value picked in one family means nothing in another. */
  let facet = null;
  let byName = false;

  function rowsBefore(g) {
    let out = g === ALL
      ? opts.groups.flatMap(x => opts.rowsOf(x).map(r => [r, x]))
      : opts.rowsOf(g).map(r => [r, g]);
    for (const t of toggles) if (t.on) out = out.filter(p => t.where(p[0]));
    return out;
  }
  function rowsFor(g) {
    const out = rowsBefore(g);
    return facet == null ? out : out.filter(p => String(opts.facet.of(p[0])) === facet);
  }

  const gq = el('input', {type: 'search', placeholder: opts.groupFilter || 'find a group...'});
  const glist = el('div', {class: 'glist'});
  const sortBtn = (label, v) => {
    const b = el('button', {class: 'gsort' + (byName === v ? ' on' : ''), text: label});
    b.onclick = () => { byName = v; paintGroups(); };
    return b;
  };
  const gsorts = el('div', {class: 'gsorts'});
  gnav.append(el('div', {class: 'ghead'}, [gq, gsorts]), glist);
  gq.oninput = () => paintGroups();

  function gitem(label, n, g, sub) {
    const b = el('button', {class: 'gitem' + (g === pickedGroup ? ' on' : '') + (n ? '' : ' zero'),
                            title: label},
                 [el('span', {class: 'gname'}, [wbr(label)]), el('span', {class: 'gn', text: num(n)})]);
    if (sub) b.append(el('span', {class: 'gsub', text: sub}));
    b.onclick = () => { pickedGroup = g; facet = null; paintGroups(); draw(); };
    return b;
  }

  function paintGroups() {
    gsorts.replaceChildren(...(opts.keepOrder ? [] : [el('span', {class: 'count', text: 'sort'}),
                                                     sortBtn('most', false), sortBtn('a–z', true)]));
    const t = gq.value.trim().toLowerCase();
    let counted = opts.groups.map(g => [g, rowsBefore(g).length]);
    const total = counted.reduce((a, p) => a + p[1], 0);
    if (t) counted = counted.filter(([g]) => (opts.chip(g) + ' ' + (opts.groupText ? opts.groupText(g) : ''))
                                               .toLowerCase().includes(t));
    if (!opts.keepOrder)
      counted.sort((a, b) => byName ? opts.chip(a[0]).localeCompare(opts.chip(b[0]))
                                    : (b[1] - a[1]) || opts.chip(a[0]).localeCompare(opts.chip(b[0])));
    const items = [];
    if (withAll && !t) items.push(gitem('all ' + opts.noun, total, ALL));
    let lastSect = null;
    for (const [g, n] of counted) {
      /* A section heading between runs of groups, where the caller has sections (the kind of
         edit in What to configure). Only in the caller's own order, where runs exist. */
      if (opts.sectionOf && opts.keepOrder) {
        const s = opts.sectionOf(g);
        if (s !== lastSect) { items.push(el('div', {class: 'gsect', text: s})); lastSect = s; }
      }
      items.push(gitem(opts.chip(g), n, g, opts.groupSub ? opts.groupSub(g) : null));
    }
    if (!items.length) items.push(el('p', {class: 'empty', text: 'no group matches'}));
    glist.replaceChildren(...items);
  }

  /* *** WHAT THE GROUP'S ROWS SAY, BEFORE YOU READ ANY OF THEM. ***
     A family of 5,820 answers is a distribution before it is a list: how many said each thing.
     Every value, with its count and a bar, and a click narrows the rows to it. */
  function facetBlock(g) {
    if (!opts.facet) return null;
    const rows = rowsBefore(g);
    const counts = {};
    for (const p of rows) { const v = String(opts.facet.of(p[0])); counts[v] = (counts[v] || 0) + 1; }
    const vals = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    if (vals.length < 2 && facet == null) return null;
    const max = Math.max(...vals.map(v => v[1]), 1);
    const box = el('div', {class: 'facets'});
    box.append(el('div', {class: 'tlab', text: opts.facet.label + ' · ' + vals.length
      + ' value(s)' + (facet != null ? '' : ' · click one to narrow the list')}));
    const grid_ = el('div', {class: 'fgrid'});
    for (const [v, n] of vals) {
      const b = el('button', {class: 'frow' + (facet === v ? ' on' : ''), title: v + ': ' + num(n)});
      b.append(el('span', {class: 'flab'}, [wbr(v)]),
               el('span', {class: 'rtrack'}, [el('span', {class: 'rfill',
                 style: 'width:' + Math.max(1.5, n / max * 100) + '%;background:' + BAR})]),
               el('span', {class: 'rval', text: num(n) + ' · ' + Math.round(n / rows.length * 100) + '%'}));
      b.onclick = () => { facet = facet === v ? null : v; draw(); };
      grid_.append(b);
    }
    box.append(grid_);
    if (facet != null) {
      const clear = el('button', {class: 'back', text: 'show every ' + opts.facet.label + ' again'});
      clear.onclick = () => { facet = null; draw(); };
      box.append(clear);
    }
    return box;
  }

  const toggleBoxes = toggles.map(t => {
    const cb = el('input', {type: 'checkbox'});
    cb.onchange = () => { t.on = cb.checked; paintGroups(); draw(); };
    return el('label', {class: 'chk', title: t.title || t.label},
              [cb, el('span', {text: t.label + ' (' + num(t.count) + ')'})]);
  });

  let list = null;
  function draw() {
    const pairs = rowsFor(pickedGroup);
    const cs = (opts.colsFor ? opts.colsFor(pickedGroup) : null) || opts.rowCols;
    list = grid(pairs, cs.map(c => ({...c,
      val: p => c.val(p[0]), cell: c.cell ? p => c.cell(p[0]) : null})), {
      placeholder: opts.rowFilter || 'filter...', page: opts.pageSize || 200,
      sort: (opts.sortFor ? opts.sortFor(pickedGroup) : null) || opts.rowSort,
      dir: (opts.dirFor ? opts.dirFor(pickedGroup) : null) || opts.rowDir || 1, scroll: 1,
      controls: toggleBoxes,
      pick: p => showOne(p[0], p[1]),
      text: p => opts.rowText(p[0]) + ' ' + opts.chip(p[1]),
      emptyText: 'nothing matches'});
    /* *** WHAT THE ROWS ARE ANSWERS TO, ABOVE THE ROWS. ***
       A group can carry a header -- the question a set of answers answered -- so the thing being
       measured is on screen rather than one click into a detail pane. */
    const gh = opts.groupHead && pickedGroup !== ALL ? opts.groupHead(pickedGroup) : null;
    head.replaceChildren(...[gh, facetBlock(pickedGroup)].filter(Boolean));
    body.replaceChildren(list);
    /* *** THE PANE OPENS ON SOMETHING. ***
       `first.click()` rather than calling `showOne` directly, so the row is also MARKED as the
       selected one -- a pane showing a row while the list shows nothing selected is two views
       of one state that disagree. */
    const first = $('tbody tr', list);
    if (first) first.click(); else blank();
  }

  function blank() {
    const plate = (DATA.cuts || {}).still;
    detail.replaceChildren(el('div', {class: 'plate'}, [
      plate ? el('img', {class: 'cut', src: plate, alt: '',
                         style: 'width:190px;opacity:.55'}) : el('span'),
      el('p', {class: 'empty', text: 'nothing matches that filter'})]));
  }

  function showOne(row, g) {
    detail.replaceChildren(...[].concat(opts.detailOf(row, g)));
    detail.scrollTop = 0;
  }

  left.append(head, body);
  cols.append(gnav, left, detail);
  top.replaceChildren(el('p', {class: 'note', text: opts.blurb}));
  host.append(top, cols);
  paintGroups();
  draw();
  /* Kept for the callers that flip a view from outside. */
  host.showGroups = () => { pickedGroup = withAll ? ALL : opts.groups[0]; facet = null; paintGroups(); draw(); };
  host.showRows = (g) => { pickedGroup = g; facet = null; paintGroups(); draw(); };
  return host;
}

function conf(x) {
  if (x == null) return el('span', {class: 'tot', text: ''});
  /* Under the 0.6 gate an answer reports nothing as a finding, so the number is the point. */
  return el('span', {class: x < 0.6 ? 'low' : '', text: x.toFixed(2)});
}

/* ------------------------------------------------------------------- the drawn lineage

   *** YOU NEVER DRAW 573 HOPS. *** That is the whole graph. A drawing is always ONE model's
   neighborhood, and measured on a 358-model warehouse those are small: median 3 boxes, p95 12,
   max 37. So three bands and straight lines, no graph algorithm, no force layout, no hairball.

   The edge label goes ON the parent box rather than on the line. With eight parents converging
   on one focus, labels on the lines overlap into mush; on the boxes they never can.

   Past BAND_MAX in a band it degrades to a list with one bracket, because 36 boxes with 36
   converging lines is the hairball this exists to avoid. That is 14 models of 358 on the parent
   side and 6 on the child side, and you can see it coming.                                    */
/* *** IT DREW NINE AND LISTED THE REST, IN A BOX THAT CLIPPED AT FIVE. ***
   The picture was a fixed-width SVG in a narrower container, so a model with five parents lost
   the fifth off the right edge, and one with ten got no picture at all. Both are the same bug:
   the drawing decided how big it needed to be and the container disagreed. The viewBox is the
   content's bounds now and the SVG fills whatever space there is, so everything is visible at
   once however many there are -- and since it is then small, you can zoom and drag.
   The cap is a real wall rather than a layout limit: past this a picture is a hairball whatever
   you do with it, and the list is the better answer. */
const BAND_MAX = 60, BW = 210, BH = 66, GAPX = 16, MINW = 860;
/* *** A BOX 148 WIDE HOLDING 22 MONOSPACE CHARACTERS IS A BOX THAT OVERFLOWS. ***
   Reported from the field with a screenshot: `int_az_parcel_sections` painted through its own
   border and then through the right edge of the drawing. Truncating by character count guesses
   at the font metrics; a clip path does not, so the box is the boundary whatever renders it.
   `MINW` keeps a two-box drawing filling its panel instead of huddling in the corner. */
const CLIP = 'boxclip';

function svg(tag, attrs, kids) {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in (attrs || {})) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  for (const c of (kids || [])) n.append(c);
  return n;
}
function svgText(x, y, s, cls) {
  const t = svg('text', {x: x, y: y, class: cls || ''}); t.textContent = s; return t;
}

function edgeNote(e) {
  const bits = [];
  if (e.kind) bits.push(e.kind);
  if (e.driving) bits.push('drives');
  if (e.union_arm) bits.push('union');
  if ((e.joined_on || []).length) bits.push('on ' + e.joined_on.join('+'));
  if (e.dropped) bits.push(e.dropped + ' dropped');
  if (e.row_loss && e.row_loss.length === 2 && e.row_loss[0])
    bits.push(pct(e.row_loss[1] / e.row_loss[0]) + ' kept');
  return bits.join(' · ');
}

function box(x, y, title, sub, cls, onclick) {
  const g = svg('g', {class: 'box ' + (cls || ''), transform: `translate(${x},${y})`});
  g.append(svg('rect', {width: BW, height: BH, rx: 5}));
  /* Two layers, and both are needed. The ellipsis says "there is more here", which a hard cut
     does not -- a name sliced mid-character just looks like a rendering fault. The clip is the
     backstop that makes the box the boundary whatever font actually renders it. */
  const cut = (t, n) => (t.length > n ? t.slice(0, n - 1) + '\u2026' : t);
  const inner = svg('g', {'clip-path': 'url(#' + CLIP + ')'});
  inner.append(svgText(11, 25, cut(title, 26), 'bt'));
  if (sub) inner.append(svgText(11, 45, cut(sub, 34), 'bs'));
  g.append(inner);
  /* `Node.append` returns undefined, so chaining `.textContent` off it sets a property on
     nothing. The hover title is how a truncated name stays readable, so it is built first. */
  const tt = svg('title', {});
  tt.textContent = title + (sub ? '\n' + sub : '');
  g.append(tt);
  if (onclick) { g.classList.add('clk'); g.onclick = onclick; }
  return g;
}

function bandList(title, edges, other) {
  /* The degradation. A table, with the same facts the boxes would have carried. */
  return el('div', {class: 'bandlist'}, [
    el('p', {class: 'note', text: title}),
    grid(edges, [
      {key: 'n', label: 'model', mono: 1, val: e => other(e), cell: e => link(other(e), 'chain')},
      {key: 'j', label: 'edge', val: e => edgeNote(e)},
    ], {placeholder: 'filter...', cap: 60})]);
}

/* *** CLICKING A NODE MUST NOT DRIVE THE SEARCH BOX. ***
   It used to jump to that model by typing its name into the filter, which left the list showing
   one row and the box full of text you then had to clear by hand to get the graph back.
   Reported from the field: "clicking a node adds it to the search thing and just FUCKS the ui...
   ideally if you're clicking a node it's just a popup right there with the relevant info."

   So a click opens a card where the node is, and going there is a deliberate second click. */
/* *** A CARD INSIDE A SCROLLING BOX GETS CUT BY THE SCROLLING BOX. ***
   It was absolutely positioned inside `.linwrap`, which needs `overflow-x: auto` for a wide
   graph, so a node near the left edge had half its card clipped away. Reported from the field
   with a screenshot: "it gets cut off bruh it needs to fit in the window".

   `position: fixed` escapes every ancestor's overflow, and then the only thing that can cut it
   is the viewport -- which is what this clamps against. Pure, so it is tested as arithmetic
   rather than by hoping a browser agrees. */
function clampToViewport(w, h, cx, below, vw, vh, pad) {
  pad = pad == null ? 12 : pad;
  const left = Math.max(pad, Math.min(cx - w / 2, vw - w - pad));
  // Below the node by preference; above it when that would run off the bottom. If it fits in
  // neither, pin to the top and let the card scroll inside itself.
  let top = below;
  if (top + h + pad > vh) top = Math.max(pad, below - h - 8);
  if (top + h + pad > vh) top = pad;
  return {left: Math.round(left), top: Math.round(top)};
}

function dismissCards() {
  document.querySelectorAll('.pop').forEach(n => n.remove());
}

function nodeCard(node, name, e) {
  dismissCards();
  const m = BY_NAME[name];
  const pop = el('div', {class: 'pop'});
  const close = el('button', {class: 'popx', text: '\u00d7'});
  close.onclick = ev => { ev.stopPropagation(); pop.remove(); };
  pop.append(close);
  pop.append(el('div', {class: 'popname mono', text: name}));
  if (!m) {
    pop.append(el('p', {class: 'note', text: 'A source, or a relation outside this project. '
      + 'assay knows it only as the other end of this hop.'}));
  } else {
    pop.append(el('div', {class: 'path mono', text: m.path}));
    if (m.description) pop.append(el('p', {class: 'prose', text: m.description.slice(0, 260)
      + (m.description.length > 260 ? '\u2026' : '')}));
    pop.append(kv([
      ['grain', fact(m.grain)],
      ['reads', String((EDGES_IN[m.uid] || []).length)],
      ['read by', String((EDGES_OUT[m.uid] || []).length)],
      ['reach', (m.exposures && m.exposures.length ? 'reaches ' + m.exposures.join(', ') + '; '
                 : '') + m.marts + ' mart(s)'],
      ['findings', el('span', {class: m.findings.length ? 'bad' : 'tot',
                               text: String(m.findings.length)})],
      ['claims', String(m.claims.length)],
    ]));
  }
  if (e) pop.append(section('this hop', el('p', {class: 'prose', text: edgeNote(e) || 'no join'})));
  const row = el('div', {class: 'bar'});
  if (m) {
    const go = el('button', {class: 'back', text: 'center the graph here'});
    go.onclick = ev => { ev.stopPropagation(); pop.remove(); GO.chain(name); };
    const go2 = el('button', {class: 'back', text: 'open in Models'});
    go2.onclick = ev => { ev.stopPropagation(); pop.remove(); open('models'); GO.models(name); };
    row.append(go, go2);
  }
  pop.append(row);
  /* On the BODY, not in the drawing: nothing an ancestor does with overflow can clip it. */
  document.body.append(pop);
  const r = node.getBoundingClientRect
    ? node.getBoundingClientRect() : {left: 0, right: 0, bottom: 0, width: 0};
  const box = pop.getBoundingClientRect ? pop.getBoundingClientRect() : {width: 340, height: 300};
  const at = clampToViewport(box.width || 340, box.height || 300,
                             (r.left + r.right) / 2, r.bottom + 8,
                             window.innerWidth || 1200, window.innerHeight || 800);
  pop.style.left = at.left + 'px';
  pop.style.top = at.top + 'px';
  return pop;
}

/* *** YOU COULD SEE WHAT FIT AND NOTHING ELSE. ***
   Drag to move, wheel or the buttons to zoom, `fit` to get back. Nothing fancy and nothing
   loaded: it moves the viewBox, which is four numbers, so it stays a single self-contained file
   that opens off a disk with no network. */
function panZoom(wrap, s, W, H) {
  let vb = {x: 0, y: 0, w: W, h: H};
  const home = {...vb};
  const apply = () => s.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);

  function zoom(by, at) {
    /* Anchor on the pointer, so zooming goes where you are looking rather than to the middle. */
    const k = Math.min(Math.max(vb.w * by, W / 12), W * 4) / vb.w;
    const cx = at ? vb.x + (at.x * vb.w) : vb.x + vb.w / 2;
    const cy = at ? vb.y + (at.y * vb.h) : vb.y + vb.h / 2;
    vb = {x: cx - (cx - vb.x) * k, y: cy - (cy - vb.y) * k, w: vb.w * k, h: vb.h * k};
    apply();
  }

  const tools = el('div', {class: 'lintools'});
  for (const [label, fn, title] of [
    ['\u2212', () => zoom(1.25), 'zoom out'],
    ['+', () => zoom(0.8), 'zoom in'],
    ['fit', () => { vb = {...home}; apply(); }, 'fit everything']]) {
    const b = el('button', {text: label, title: title});
    b.onclick = ev => { ev.stopPropagation(); fn(); };
    tools.append(b);
  }
  wrap.append(tools);
  wrap.append(el('div', {class: 'linhint', text: 'drag to move · scroll to zoom'}));

  wrap.addEventListener('wheel', ev => {
    ev.preventDefault();
    const r = s.getBoundingClientRect();
    zoom(ev.deltaY > 0 ? 1.12 : 0.89,
         {x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height});
  }, {passive: false});

  /* *** A TAP HAS TO STAY A TAP. ***
     Capturing the pointer on `pointerdown` retargets every later event for that pointer to the
     SVG ROOT, so the `click` lands on the canvas and never on the node `<g>`. The node handlers
     were still attached and still correct; they simply could not fire, and every node on the
     chain tab stopped being clickable without anything about them changing.

     So the press only ARMS a pan. Capture is taken on the first move past a few pixels, which is
     the point where the gesture is unambiguously a drag rather than a click. */
  const DRAG_PX = 4;
  let from = null, dragging = false;
  s.addEventListener('pointerdown', ev => {
    from = {x: ev.clientX, y: ev.clientY, vx: vb.x, vy: vb.y, id: ev.pointerId};
    dragging = false;
  });
  s.addEventListener('pointermove', ev => {
    if (!from) return;
    const dx = ev.clientX - from.x, dy = ev.clientY - from.y;
    if (!dragging) {
      if (Math.abs(dx) < DRAG_PX && Math.abs(dy) < DRAG_PX) return;
      dragging = true;
      s.classList.add('drag');
      try { s.setPointerCapture(from.id); } catch (e) { /* the pointer is already gone */ }
    }
    const r = s.getBoundingClientRect();
    vb.x = from.vx - dx * (vb.w / r.width);
    vb.y = from.vy - dy * (vb.h / r.height);
    apply();
  });
  for (const done of ['pointerup', 'pointercancel', 'pointerleave'])
    s.addEventListener(done, ev => {
      if (dragging) {
        try { s.releasePointerCapture(from ? from.id : ev.pointerId); } catch (e) { /* gone */ }
      }
      from = null; dragging = false; s.classList.remove('drag');
    });
  apply();
}

function lineage(m) {
  const ins = (EDGES_IN[m.uid] || []).slice(), outs = (EDGES_OUT[m.uid] || []).slice();
  /* Driving parents first: the driving edge is the spine, and everything else hangs off it.
     Then by name, so the picture is the same on every machine and in every rerun. */
  ins.sort((a, b) => (b.driving - a.driving) || a.parent_name.localeCompare(b.parent_name));
  outs.sort((a, b) => a.child_name.localeCompare(b.child_name));

  const host = el('div');
  const wrap = el('div', {class: 'linwrap'});
  const drawIn = ins.length <= BAND_MAX, drawOut = outs.length <= BAND_MAX;
  const nTop = drawIn ? ins.length : 0, nBot = drawOut ? outs.length : 0;
  /* *** TWENTY PARENTS IN ONE ROW IS A DRAWING NOBODY CAN READ. ***
     The band spread across a viewBox as wide as it needed, and `meet` then zoomed the whole
     thing out until the node text was too small to read -- "its just a big fuckin mess it needs
     to be like a 5x4 grid or something with enough space". A band wraps now, so twenty parents
     are four rows of five and the drawing stays near enough to square that fitting it keeps the
     names legible. */
  const PER_ROW = 5, GAPY = 26;
  const rowsIn = Math.ceil(nTop / PER_ROW), rowsOut = Math.ceil(nBot / PER_ROW);
  const cols = Math.min(PER_ROW, Math.max(nTop, nBot, 1));
  const W = Math.max(cols * (BW + GAPX) + GAPX, MINW);
  const bandH = r => Math.max(r, 0) * (BH + GAPY);
  /* The focus sits below the parents with room for the connectors, wherever that lands. */
  const topH = bandH(rowsIn), gapToFocus = 62;
  const fyTop = topH + (rowsIn ? gapToFocus : 20);
  const H = fyTop + BH + (rowsOut ? gapToFocus : 20) + bandH(rowsOut);
  /* The SVG fills the viewport and the viewBox is the drawing, so everything fits at any count.
     Sizing the element to the drawing instead is what clipped the fifth parent off the edge. */
  const s = svg('svg', {class: 'lin', viewBox: `0 0 ${W} ${H}`,
                        preserveAspectRatio: 'xMidYMid meet'});
  s.append(svg('defs', {}, [
    svg('marker', {id: 'ah', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7,
        markerHeight: 7, orient: 'auto'}, [svg('path', {d: 'M0,0 L8,4 L0,8 z'})]),
    svg('clipPath', {id: CLIP}, [svg('rect', {width: BW - 6, height: BH})])]));

  const fx = (W - BW) / 2, fy = fyTop;
  /* Where box `i` of `n` sits in a wrapped band. The last row is centred on its own count, so a
     band of twelve is 5 + 5 + 2 with the two in the middle rather than shoved left. */
  const place = (i, n, yTop) => {
    const row = Math.floor(i / PER_ROW), col = i % PER_ROW;
    const inRow = Math.min(PER_ROW, n - row * PER_ROW);
    return {x: (W - (inRow * (BW + GAPX) - GAPX)) / 2 + col * (BW + GAPX),
            y: yTop + row * (BH + GAPY)};
  };

  if (drawIn) ins.forEach((e, i) => {
    const at = place(i, ins.length, 0);
    s.append(svg('path', {class: 'ln' + (e.driving ? ' drv' : '') + (why(e) ? ' nb' : ''),
      'marker-end': 'url(#ah)',
      d: `M${at.x + BW / 2},${at.y + BH} C${at.x + BW / 2},${at.y + BH + 40} ${fx + BW / 2},${fy - 40} ${fx + BW / 2},${fy - 6}`}));
    s.append(box(at.x, at.y, e.parent_name, edgeNote(e), 'par' + (why(e) ? ' nb' : ''),
                 ev => { ev.stopPropagation();
                   nodeCard(ev.currentTarget, e.parent_name, e); }));
  });
  if (drawOut) outs.forEach((e, i) => {
    const at = place(i, outs.length, fy + BH + gapToFocus);
    s.append(svg('path', {class: 'ln', 'marker-end': 'url(#ah)',
      d: `M${fx + BW / 2},${fy + BH} C${fx + BW / 2},${fy + BH + 40} ${at.x + BW / 2},${at.y - 40} ${at.x + BW / 2},${at.y - 6}`}));
    s.append(box(at.x, at.y, e.child_name, edgeNote(e), 'chi',
                 ev => { ev.stopPropagation();
                   nodeCard(ev.currentTarget, e.child_name, e); }));
  });
  const g = m.grain ? (Array.isArray(m.grain.value) ? m.grain.value.join(', ') : String(m.grain.value)) : 'grain not settled';
  /* *** THE MODEL THE PICTURE IS ABOUT WAS THE ONE BOX YOU COULD NOT CLICK. ***
     Every parent and child opened a card and the focus node was drawn with no handler at all. */
  s.append(box(fx, fy, m.name, g, 'foc',
               ev => { ev.stopPropagation(); nodeCard(ev.currentTarget, m.name, null); }));

  if (!drawIn && ins.length)
    host.append(bandList(ins.length + ' parents, too many to draw. The same facts as a list:',
                         ins, e => e.parent_name));
  wrap.append(s);
  /* Clicking the canvas anywhere but a node dismisses the card, which is what people expect and
     is also the only way out on a touch device. */
  wrap.onclick = () => dismissCards();
  panZoom(wrap, s, W, H);
  host.append(wrap);
  if (!drawOut && outs.length)
    host.append(bandList(outs.length + ' children, too many to draw:', outs, e => e.child_name));
  if (!ins.length && !outs.length)
    host.append(el('p', {class: 'empty', text: 'This model has no edges recorded: nothing reads it and it reads nothing that assay could resolve.'}));
  return host;
}

/* ------------------------------------------------------------------------------- Models */
function modelsTab(host) {
  const detail = el('div', {class: 'detail'});
  const list = grid(M, [
    {key: 'name', label: 'model', mono: 1, val: m => m.name},
    {key: 'layer', label: 'layer', val: m => m.layer},
    {key: 'feeds', label: 'feeds', n: 1, val: m => (m.exposures || []).length,
     cell: m => el('span', {class: (m.exposures || []).length ? '' : 'tot',
                            title: (m.exposures || []).join(', '),
                            text: num((m.exposures || []).length)})},
    {key: 'marts', label: 'marts', n: 1, val: m => m.marts},
    {key: 'findings', label: 'find', n: 1, val: m => m.findings.length,
     cell: m => el('span', {class: m.findings.length ? 'bad' : 'tot',
                            text: num(m.findings.length || 0)})},
  ], {placeholder: 'filter models, paths, descriptions...', scroll: 1, pick: m => show(m),
      where: m => mine(m), controls: [packageFilter(() => list.redraw())].filter(Boolean),
      text: m => [m.name, m.path, m.layer, m.description].join(' ')});

  function show(m) {
    const d = detail; d.replaceChildren();
    d.append(el('h2', {text: m.name}));
    d.append(el('div', {class: 'path mono', text: m.path}));
    if (m.unreadable) d.append(el('p', {class: 'pill bad',
      text: 'assay could not read this model, so it is absent from everything below. That is not a pass.'}));
    if (m.description) d.append(md(m.description));

    d.append(section('what one row is', kv([
      ['grain', fact(m.grain)],
      ['the SQL says', m.derived_grain.length
        ? el('span', {class: 'mono', text: m.derived_grain.join(', ')})
        : el('span', {class: 'tot', text: 'nothing settles it'})],
      ['materialized', m.materialized],
      ['reach', el('span', {text: m.marts + ' mart(s), ' + m.descendants + ' descendant(s)'})],
      /* What outside the warehouse this model feeds, in the project's own words. Absent is not
         "nothing reads it": it is "no exposure in the yml says so". */
      ['feeds', (m.exposures || []).length
        ? el('span', {text: m.exposures.join(', ')})
        : el('span', {class: 'tot', text: 'no exposure declares it'})],
    ])));

    const colCols = [
      {key: 'name', label: 'column', mono: 1, val: c => c.name,
       cell: c => { const s = el('span', {}); s.append(el('span', {text: c.name + ' '}));
         if (c.in_key) s.append(el('span', {class: 'pill on', text: 'key'})); return s; }},
    ];
    if (HAS_ROLES) colCols.push(
      {key: 'role', label: 'role', val: c => c.role && c.role.value, cell: c => fact(c.role)});
    colCols.push(
      {key: 'prov', label: 'came from', val: c => c.provenance && c.provenance.value,
       cell: c => fact(c.provenance)},
      {key: 'why', label: 'how assay knows', val: c => (c.provenance || {}).note || '',
       cell: c => el('span', {class: 'tot', text: (c.provenance || {}).note || ''})});
    const colsBox = el('div', {}, [grid(m.columns, colCols,
      {placeholder: 'filter columns...', cap: 400, emptyText: 'no columns known'})]);
    if (!HAS_ROLES) colsBox.append(el('p', {class: 'note',
      text: 'No column has a role yet, on any model: nothing has asked. `assay columns` answers '
        + 'what job each column does -- identifier, measure, qualifier, timestamp -- and it is '
        + 'what several grain findings need before they can fire at all.'}));
    d.append(section('columns (' + m.columns.length + ')', colsBox));

    const ins = EDGES_IN[m.uid] || [], outs = EDGES_OUT[m.uid] || [];
    const lin = el('button', {class: 'back', text: 'see it drawn →'});
    lin.onclick = () => { open('chain'); GO.chain(m.name); };
    d.append(section('reads ' + ins.length + ', read by ' + outs.length, el('div', {}, [
      lin,
      grid(ins.concat(outs), [
        {key: 'dir', label: '', val: e => e.child === m.uid ? 'reads' : 'read by',
         cell: e => el('span', {class: 'pill', text: e.child === m.uid ? 'reads' : 'read by'})},
        {key: 'other', label: 'model', mono: 1,
         val: e => e.child === m.uid ? e.parent_name : e.child_name,
         cell: e => link(e.child === m.uid ? e.parent_name : e.child_name, 'chain')},
        {key: 'edge', label: 'edge', val: e => edgeNote(e)},
        {key: 'dropped', label: 'dropped', n: 1, val: e => e.dropped,
         cell: e => { if (!e.dropped) return el('span', {class: 'tot', text: '0'});
           const t = el('details'); t.append(el('summary', {text: num(e.dropped)}));
           t.append(el('pre', {text: (e.dropped_cols || []).join('\n')})); return t; }},
      ], {placeholder: 'filter hops...', cap: 200,
          emptyText: 'no edges: a leaf that nothing reads'})])));

    const fs = m.findings.map(i => FIND[i]).filter(Boolean);
    d.append(section('findings (' + fs.length + ')', fs.length ? grid(fs, [
      {key: 'check', label: 'check', mono: 1, val: f => f.check},
      {key: 'what', label: 'what', val: f => f.summary},
      {key: 'ruled', label: 'ruled', val: f => f.ruled_finding ? 1 : 0,
       cell: f => el('span', {class: 'pill ' + (f.ruled_finding ? 'on' : ''),
         text: f.ruled_finding ? 'read by a person'
           : (f.ruled_model ? 'model ruled, not this' : 'unread')})},
    ], {placeholder: 'filter findings...', cap: 200})
      : el('p', {class: 'empty', text: 'nothing found on this model'})));

    const cs = m.claims.map(i => CLAIM[i]).filter(Boolean);
    const bad = cs.filter(c => c.contradicted != null).length;
    d.append(section('claims (' + cs.length + (bad ? ', ' + bad + ' contradicted' : '') + ')',
      cs.length ? grid(cs, [
        {key: 'text', label: 'claim', clip: 1, val: c => c.text},
        {key: 'where', label: 'written', mono: 1, val: c => c.source_ref},
        {key: 'v', label: 'the code', val: c => c.contradicted == null ? -1 : c.contradicted,
         cell: c => c.contradicted == null ? el('span', {class: 'tot', text: 'not contradicted'})
           : el('span', {class: 'pill bad', text: 'contradicts @' + c.contradicted.toFixed(2)})},
      ], {placeholder: 'filter claims...', cap: 300})
        : el('p', {class: 'empty', text: 'no claims extracted for this model'})));

    const ds = m.decisions.map(i => DATA.decisions[i]).filter(Boolean);
    d.append(section('answers (' + ds.length + ')', ds.length ? grid(ds, [
      {key: 'q', label: 'question', mono: 1, val: a => a.question},
      {key: 'a', label: 'answered', val: a => a.answer},
      {key: 'c', label: 'conf', n: 1, val: a => a.confidence, cell: a => conf(a.confidence)},
      {key: 'r', label: 'next best', val: a => a.runner_up && a.runner_up[0],
       cell: a => a.runner_up ? el('span', {class: 'tot',
         text: a.runner_up[0] + ' ' + a.runner_up[1].toFixed(2)}) : el('span')},
      {key: 'ctx', label: 'about', clip: 1, val: a => a.context},
    ], {placeholder: 'filter answers...', cap: 400})
      : el('p', {class: 'empty', text: 'nothing has been asked about this model'})));
  }

  host.replaceChildren(
    el('p', {class: 'note', text: 'Every model this project builds, and what one row of each one is.'}),
    el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a model. Everything assay knows about it is here: what one row is and who settled that, every column with its role and where its value came from, every hop in and out, what the project claims about it, and every answer ever given.'}));
  GO.models = name => { const m = BY_NAME[name]; if (!m) return;
    show(m);
    highlight('#p-models', name); };
  const first = $('tbody tr', list); if (first) first.click();
}

/* *** "209 OF 573 WORTH A LOOK" IS NOT A SIGNAL, IT IS THE TABLE. ***
   163 of those 209 came from one rule of mine: "a driving edge joining on nothing". A driving
   edge IS the FROM clause. Of course it joins on nothing. It was flagging the normal case, which
   is how a list of exceptions becomes a list.

   Measured on 573 real hops before rewriting it -- dropped columns p75 12, p90 24, max 233; a
   join carrying no resolvable key 46; judged fan-outs 13 -- and every threshold below is a number
   off that distribution rather than a guess. A hop with nothing to say returns "" and is silent. */
function why(e) {
  const out = [];
  if (!e.driving && !e.union_arm && !(e.joined_on || []).length)
    out.push((e.kind || 'a') + ' join carrying no key assay could resolve');
  if (e.dropped > 60) out.push('drops ' + e.dropped + ' columns');
  if (e.row_loss && e.row_loss.length === 2 && e.row_loss[0] &&
      e.row_loss[1] / e.row_loss[0] < 0.5)
    out.push('keeps only ' + pct(e.row_loss[1] / e.row_loss[0]) + ' of the parent');
  return out.join(' \u00b7 ');
}
const NOTABLE_BY_CHILD = {};
for (const e of DATA.edges) if (why(e))
  (NOTABLE_BY_CHILD[e.child] = NOTABLE_BY_CHILD[e.child] || []).push(e);

/* --------------------------------------------------------------------------- The chain */
function chainTab(host) {
  const detail = el('div', {class: 'detail'});
  const withEdges = M.filter(m => (EDGES_IN[m.uid] || []).length || (EDGES_OUT[m.uid] || []).length);
  const nOf = m => (NOTABLE_BY_CHILD[m.uid] || []).length;
  let onlyNotable = false;

  /* *** THE OBSERVABILITY BELONGS IN THE PAGE, NOT BEHIND A DISCLOSURE TRIANGLE. ***
     It was a collapsed `<details>` above the list, which is the same shape as every
     absence-reads-as-nothing defect in this codebase: a closed summary and a check that found
     nothing look identical. So the count sits in the model list as a column you can sort on, the
     edges are drawn differently, and each model's own notable hops are named under its drawing. */
  /* A checkbox, because it is a toggle. A button whose label flips between two sentences makes
     you read it to find out which state you are in; a checkbox shows you. */
  const cb = el('input', {type: 'checkbox'});
  const toggle = el('label', {class: 'chk'}, [cb, el('span',
    {text: 'only the ' + Object.keys(NOTABLE_BY_CHILD).length
           + ' models with something notable'})]);
  function paintToggle() { cb.checked = onlyNotable; }
  cb.onchange = () => { onlyNotable = cb.checked; list.redraw(); };
  paintToggle();

  const list = grid(withEdges, [
    {key: 'name', label: 'model', mono: 1, val: m => m.name},
    {key: 'in', label: 'reads', n: 1, val: m => (EDGES_IN[m.uid] || []).length},
    {key: 'out', label: 'read by', n: 1, val: m => (EDGES_OUT[m.uid] || []).length},
    {key: 'note', label: 'notable', n: 1, val: m => nOf(m),
     cell: m => el('span', {class: nOf(m) ? 'low' : 'tot', text: nOf(m) ? num(nOf(m)) : ''})},
  ], {placeholder: 'filter models...', scroll: 1, pick: m => show(m), sort: 'name',
      where: m => mine(m) && (!onlyNotable || nOf(m)),
      controls: [toggle, packageFilter(() => list.redraw())].filter(Boolean),
      text: m => m.name + ' ' + m.path});

  function show(m) {
    const mine = NOTABLE_BY_CHILD[m.uid] || [];
    const d = detail; d.replaceChildren();
    d.append(el('h2', {text: m.name}));
    d.append(el('div', {class: 'path mono', text: m.path}));
    d.append(lineage(m));
    if (mine.length) {
      d.append(section('worth a look on this model (' + mine.length + ')', grid(mine, [
        {key: 'parent', label: 'from', mono: 1, val: e => e.parent_name,
         cell: e => link(e.parent_name, 'chain')},
        {key: 'why', label: 'why', val: e => why(e),
         cell: e => el('span', {class: 'low', text: why(e)})},
        {key: 'on', label: 'joined on', mono: 1, val: e => (e.joined_on || []).join(', '),
         cell: e => (e.joined_on || []).length
           ? el('span', {class: 'mono', text: e.joined_on.join(', ')})
           : el('span', {class: 'tot', text: 'nothing resolvable'})},
      ], {placeholder: 'filter...', cap: 60})));
    }
    const all = (EDGES_IN[m.uid] || []).concat(EDGES_OUT[m.uid] || []);
    d.append(section('every hop, in and out (' + all.length + ')', grid(all, [
      {key: 'dir', label: '', val: e => e.child === m.uid ? 'reads' : 'read by',
       cell: e => el('span', {class: 'pill', text: e.child === m.uid ? 'reads' : 'read by'})},
      {key: 'other', label: 'model', mono: 1,
       val: e => e.child === m.uid ? e.parent_name : e.child_name,
       cell: e => link(e.child === m.uid ? e.parent_name : e.child_name, 'chain')},
      {key: 'edge', label: 'edge', val: e => edgeNote(e)},
      {key: 'dropped', label: 'dropped', n: 1, val: e => e.dropped,
       cell: e => { if (!e.dropped) return el('span', {class: 'tot', text: '0'});
         const t = el('details'); t.append(el('summary', {text: num(e.dropped)}));
         t.append(el('pre', {text: (e.dropped_cols || []).join('\n')})); return t; }},
    ], {placeholder: 'filter hops...', cap: 200, emptyText: 'no edges'})));
  }

  const n = DATA.edges.filter(e => why(e)).length;
  host.replaceChildren(
    el('p', {class: 'note', text: 'Every hop in the DAG, drawn one neighborhood at a time. '
             + n + ' of ' + DATA.edges.length + ' hops carry something worth a look: a join with '
             + 'no key assay could resolve, an unusually large column drop, or most of the parent '
             + 'lost. Those are counted in the notable column and named under each drawing.'}),
    el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a model to see its lineage drawn: what feeds it, what it feeds, and what each edge carries and drops. A drawing is always one neighborhood, never the whole DAG.'}));
  /* *** THE DETAIL IS AUTHORITATIVE; THE LIST IS AN INDEX. ***
     Going to a model used to work by TYPING ITS NAME INTO THE FILTER, which left the list showing
     one row and the box full of text somebody had to clear by hand before they could see anything
     else. Show the model, then highlight its row if it happens to be on screen. Nothing the user
     typed is touched. */
  GO.chain = name => { const m = BY_NAME[name]; if (!m) return;
    show(m);
    highlight('#p-chain', name); };
  const first = $('tbody tr', list); if (first) first.click();
}

/* ------------------------------------------------------------------------------- Claims */
function claimsTab(host) {
  const byModel = {};
  for (const c of DATA.claims) {
    const g = byModel[c.subject_name] = byModel[c.subject_name] ||
      {model: c.subject_name, rows: [], bad: 0,
       // *** THE GROUP IS A MODEL, SO IT SHOULD SAY WHAT THE MODEL IS. ***
       // A list of 349 names and two counts makes you click to find out whether you care.
       desc: ((BY_NAME[c.subject_name] || {}).description || '').split('\n')[0]};
    g.rows.push(c); if (c.contradicted != null) g.bad++;
  }
  const groups = Object.values(byModel).sort((a, b) => a.model.localeCompare(b.model));
  const rowCols = [
    {key: 'model', label: 'model', mono: 1, val: c => c.subject_name,
     cell: c => link(c.subject_name)},
    {key: 'text', label: 'claim', clip: 1, val: c => c.text},
    /* *** A LEFT PANE IS AN INDEX, NOT A SECOND REPORT. ***
       Five columns in this pane clipped the last two off the edge, and one of them was the
       verdict -- the thing somebody scans this list FOR. The kind and the file have room in the
       pane on the right, one click away. */
    {key: 'v', label: 'code', n: 1, val: c => c.contradicted == null ? -1 : c.contradicted,
     cell: c => c.contradicted == null ? el('span', {class: 'tot', text: '\u2014'})
       : el('span', {class: 'pill bad', text: c.contradicted.toFixed(2)})},
  ];
  const contradicted = DATA.claims.filter(c => c.contradicted != null);

  const d = drill({
    noun: 'claims', groups: groups, chip: g => g.model,
    /* The one view worth keeping beside "by model": the claims the code disagrees with. It
       combines with a model chip rather than replacing the view, which the old dropdown could
       not do -- picking `contradicted` there threw away whichever model you were looking at. */
    groupFilter: 'find a model...',
    groupText: g => g.desc,
    groupSub: g => g.bad ? num(g.bad) + ' contradicted by the code' : null,
    facet: {label: 'kind', of: c => c.kind || 'unclassified'},
    toggles: [{label: 'only where the code contradicts', count: contradicted.length,
               title: 'only claims a judgment read against the SQL and found contradicted',
               where: c => c.contradicted != null}],
    rowFilter: 'filter claims...',
    blurb: 'Every sentence this project says about itself, extracted from descriptions and SQL '
      + 'comments, grouped by the model it is about. A claim with no verdict was never asked, '
      + 'which is not the same as supported.',
    rowsOf: g => g.rows, rowCols: rowCols, rowSort: 'v', rowDir: -1,
    rowText: c => [c.text, c.source_ref, c.kind].join(' '),
    /* The claim itself is a SENTENCE, and a sentence in a table cell is a sentence you skim.
       The right pane is where it gets read. */
    detailOf: c => [
      el('h2', {text: c.subject_name}),
      el('div', {class: 'path', text: c.source_ref || ''}),
      el('div', {}, [el('span', {class: 'pill', text: c.kind || 'unclassified'})]),
      section('what it says', el('p', {class: 'quote', text: c.text})),
      section('has the code contradicted it',
        c.contradicted == null
          ? el('p', {class: 'note', text: 'Never asked. Not the same as supported: no question '
              + 'about this claim has been put to a model, so there is no answer to trust.'})
          : el('p', {class: 'note'}, [
              el('span', {class: 'pill bad', text: 'contradicts @' + c.contradicted.toFixed(2)}),
              el('span', {text: ' The SQL was read against this sentence and they disagree.'})])),
    ],
  });
  host.replaceChildren(d);
}

/* -------------------------------------------------------------------------------- Areas */
/* *** A CLUSTER IS AN AREA, NOT A FINDING, AND NOTHING HERE IS RULED ON AS ONE. ***
   What code settled is exact and free; what a judgment read is shown beside it with how sure it
   was, and a cluster read as one rule is ruled on member by member, in the Findings tab. */
function areasTab(host) {
  const A = DATA.areas || {};
  const pcs = A.predicate_clusters || [], odds = A.odd_ones_out || [], same = A.same_claim || [];
  const read = r => r ? el('span', {class: 'pill judged',
                                    text: r.answer.replace(/_/g, ' ') + ' @' + r.confidence})
                      : el('span', {class: 'tot', text: 'not asked'});
  const models = ms => el('span', {}, ms.map(m => link(m)).flatMap((x, i) =>
    i ? [el('span', {text: ', '}), x] : [x]));
  /* *** THREE LISTS UNDER ONE NUMBER, AND THE NUMBER WAS THE FIRST LIST'S. ***
     The tab said 16, which was the filters; the same page also held "one claim, several models
     (39)" and the odd ones out, stacked below, each with its own count. Reported as "kinda
     confusing? def gets lost". They are three groups now, in the group column every other
     high-volume tab uses, the tab's number is all three, and one list shows at a time. */
  const groups = [
    {key: 'filters', label: 'filters written the same way', rows: pcs,
     cols: [
       {key: 'size', label: 'models', n: 1, val: c => c.size},
       {key: 'shape', label: 'filter', mono: 1, clip: 1, val: c => c.shape},
       {key: 'one', label: 'one rule?', val: c => c.one_rule ? c.one_rule.answer : '',
        cell: c => c.macro_at ? el('span', {class: 'pill declared', text: 'written once'})
                              : read(c.one_rule)}],
     sort: 'size', dir: -1},
    {key: 'odd', label: 'the one that differs', rows: odds,
     cols: [
       {key: 'model', label: 'model', mono: 1, val: o => o.model, cell: o => link(o.model)},
       {key: 'diff', label: 'what differs', clip: 1, val: o => o.difference},
       {key: 'read', label: 'read as', val: o => o.read_as ? o.read_as.answer : '',
        cell: o => read(o.read_as)}],
     sort: 'model', dir: 1},
    {key: 'same', label: 'one claim, several models', rows: same,
     cols: [
       {key: 'n', label: 'models', n: 1, val: g => g.length},
       {key: 'claim', label: 'claim', clip: 1, val: g => g[0].claim}],
     sort: 'n', dir: -1},
  ];
  const kind = r => Array.isArray(r) ? 'same' : (r.shape != null ? 'filters' : 'odd');

  host.replaceChildren(drill({
    noun: 'areas', groups: groups, all: false, keepOrder: 1,
    chip: g => g.label, groupFilter: 'find a list...',
    rowsOf: g => g.rows, rowCols: groups[0].cols,
    colsFor: g => g.cols, sortFor: g => g.sort, dirFor: g => g.dir,
    rowFilter: 'filter by filter text, claim or model...',
    rowText: r => kind(r) === 'same' ? r.map(x => x.model + ' ' + x.claim).join(' ')
      : kind(r) === 'filters' ? [r.shape, r.models.join(' ')].join(' ')
      : [r.model, r.difference, r.shared, r.this].join(' '),
    blurb: 'Filters written the same way in several models, the one that differs from what most '
      + 'of its family writes, and one claim made about several models. Grouping is exact and '
      + 'free; the readings come from `assay clusters --judge`.',
    detailOf: r => {
      const k = kind(r);
      if (k === 'filters') return [
        el('h2', {class: 'mono', text: r.shape}),
        section('where', kv([
          ['models', models(r.models)],
          ['written once?', r.macro_at || 'no: each model writes it'],
          ['one rule?', r.macro_at ? el('span', {class: 'tot', text: 'settled: it is one macro'})
                                   : read(r.one_rule)],
          ['fix belongs', r.macro_at ? el('span', {class: 'tot', text: '—'})
                                     : read(r.fix_belongs)]]))];
      if (k === 'odd') return [
        el('h2', {}, [link(r.model)]),
        el('p', {class: 'prose', text: r.difference}),
        section('what it writes', el('pre', {text: r.this || ''})),
        section('what the others write', el('pre', {text: r.shared || ''})),
        section('the others', models(r.shared_by || [])),
        section('read as', read(r.read_as))];
      /* The members' own words: the claim is "the same" by a judgment, not by string equality,
         so each wording is shown as it was written -- once, with every model that wrote it,
         because ten models writing one sentence is one wording and not ten. */
      const byText = {};
      for (const x of r) (byText[x.claim] = byText[x.claim] || []).push(x.model);
      const words = Object.entries(byText);
      return [
        el('h2', {text: 'one claim, ' + r.length + ' models'}),
        section(words.length === 1 ? 'what every one of them says'
                                   : words.length + ' wordings of it', el('div', {}, words.map(
          ([t, ms]) => el('div', {class: 'opt'}, [el('p', {class: 'quote', text: t}),
                                                  models(ms)]))))];
    },
  }));
}

/* ----------------------------------------------------------------------------- Findings */
function findingsTab(host) {
  const detail = el('div', {class: 'detail'});
  let only = null;
  const byCheck = {};
  for (const f of DATA.findings) {
    const g = byCheck[f.check] = byCheck[f.check] ||
      {check: f.check, n: 0, marts: 0, ruled: 0, rests_on: f.rests_on};
    g.n++; g.marts = Math.max(g.marts, f.marts); g.ruled += f.ruled_finding ? 1 : 0;
  }
  const checks = Object.values(byCheck).sort((a, b) => b.n - a.n);

  /* *** ELEVEN CHIPS, EACH CARRYING A NAME, A COUNT AND "0 read", IS A WALL OF TEXT. ***
     Reported from the field with a screenshot: two rows of controls above the thing they filter,
     spending more space than the table. A select says the same thing in one line and sits in the
     filter bar that already exists, so nothing is added above the list at all. */
  const pickCheck = el('select');
  const opt = (v, t) => { const o = document.createElement('option'); o.value = v; o.textContent = t;
    return o; };
  pickCheck.append(opt('', 'every check \u00b7 ' + DATA.findings.length));
  for (const c of checks)
    pickCheck.append(opt(c.check, c.check + ' \u00b7 ' + c.n + (c.ruled ? ' \u00b7 ' + c.ruled + ' read' : '')));
  pickCheck.onchange = () => { only = pickCheck.value || null; list.redraw(); };

  const list = grid(DATA.findings, [
    {key: 'check', label: 'check', mono: 1, val: f => f.check},
    {key: 'model', label: 'model', mono: 1, val: f => f.model, cell: f => link(f.model)},
    {key: 'w', label: 'weight', n: 1, val: f => f.weight,
     cell: f => el('span', {text: f.weight.toFixed(1)})},
    {key: 'feeds', label: 'feeds', n: 1, val: f => (f.exposures || []).length,
     cell: f => el('span', {class: (f.exposures || []).length ? '' : 'tot',
                            title: (f.exposures || []).join(', '),
                            text: num((f.exposures || []).length)})},
    {key: 'marts', label: 'marts', n: 1, val: f => f.marts},
  ], {placeholder: 'filter findings...', scroll: 1, sort: 'w', dir: -1, pick: f => show(f),
      where: f => !only || f.check === only, controls: [pickCheck],
      text: f => [f.check, f.model, f.summary].join(' ')});


  function show(f) {
    detail.replaceChildren(
      el('h2', {text: f.check}),
      el('div', {class: 'path'}, [link(f.model), el('span', {class: 'mono tot',
        text: '  ·  ' + (f.file || '')})]),
      el('p', {class: 'prose', text: f.summary}),
      section('what it means', md(f.detail || '')),
      section('severity', kv([
        ['weight', String(f.weight)],
        ['reaches', (f.exposures || []).length ? f.exposures.join(', ')
          : el('span', {class: 'tot', text: 'no exposure'})],
        /* First SEEN by a full check, at the commit HEAD was on. Not "introduced": the commit that
           introduced it can be earlier, and `backtest` is what finds that. */
        ...(f.first_seen ? [['first seen', el('span', {text: f.first_seen.at +
            (f.first_seen.commit ? ' at ' + f.first_seen.commit : '') +
            (f.first_seen.subject ? ' \u00b7 ' + f.first_seen.subject.slice(0, 80) : '')})]] : []),
        ...(f.group ? [['same construct', el('span', {text: f.group.size + ' models (' +
            f.group.models.slice(0, 5).join(', ') + (f.group.size > 5 ? ', ...' : '') + ') — ' +
            (f.group.macro_at ? 'one edit in ' + f.group.macro_at : 'written inline in each')})]]
          : []),
        ['marts downstream', String(f.marts)],
        ['descendants', String(f.descendants)],
        ['rests on', f.rests_on
          ? el('span', {class: 'pill judged', text: f.rests_on})
          : el('span', {class: 'pill declared', text: 'structural: a parser decided it'})],
        ['ruled', el('span', {class: 'pill ' + (f.ruled_finding ? 'on' : ''),
          text: f.ruled_finding ? 'a person read this finding'
            : (f.ruled_model ? 'a person ruled on this model, but not on this finding'
                             : 'nobody has read it')})],
      ])),
      section('evidence', kvAny(f.evidence)),
      section('rule on it', el('p', {class: 'mono prose',
        text: "assay review -i\nrule(finding='" + f.id + "', verdict=..., why=...)"})));
  }

  host.replaceChildren(
    el('p', {class: 'note', text: 'Ranked by weight, which is the base severity lifted by reach: '
             + 'the same defect on a leaf and on a model nine marts read are not the same '
             + 'finding.'}),
    el('div', {class: 'wrap2 wide'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a finding.'}));
  const first = $('tbody tr', list); if (first) first.click();
}

/* ------------------------------------------------------------------------------ Answers */
/* `model.p.orders::claim::a1b2c3` -> {name: 'orders', scope: 'claim'}. The grammar is documented
   in SCHEMA.md and is string convention rather than constraint, so it is decomposed in ONE place
   rather than re-split at each call site. */
function subjectOf(a) {
  const key = String(a.key || '');
  const cut = key.indexOf('::');
  const head = cut === -1 ? key : key.slice(0, cut);
  const rest = cut === -1 ? '' : key.slice(cut + 2);
  return {name: head.split('.').pop() || key, scope: rest.split('::')[0] || ''};
}

function answersTab(host) {
  const fam = {};
  for (const a of DATA.decisions) {
    const p = a.question.split('__')[0];
    const g = fam[p] = fam[p] || {prefix: p, rows: [], sum: 0, n: 0, low: 0, versions: {}};
    g.rows.push(a);
    if (a.confidence != null) { g.sum += a.confidence; g.n++; if (a.confidence < 0.6) g.low++; }
    if (a.prompt_version) g.versions[a.prompt_version] = 1;
  }
  const qByPrefix = {};
  for (const q of DATA.questions) if (q.id_prefix) qByPrefix[q.id_prefix] = q.name;
  const groups = Object.values(fam).sort((a, b) => b.rows.length - a.rows.length);

  const qOf = g => (DATA.questions || []).find(x => x.id_prefix === g.prefix);
  host.replaceChildren(drill({
    noun: 'answers', groups: groups, rowFilter: 'filter answers...',
    chip: g => qByPrefix[g.prefix] || g.prefix,
    groupFilter: 'find a question...',
    groupSub: g => g.low ? num(g.low) + ' under 0.60' : null,
    facet: {label: 'answered', of: a => a.answer == null ? '(no answer)' : a.answer},
    /* *** GROUPED BY QUESTION, AND THE QUESTION IS ON THE SCREEN. ***
       Opening on "all answers" put thousands of rows from every family in one list with no
       question anywhere above them. It opens on the largest question, with that question's own
       words over its answers and how many were unsure. */
    startGroup: gs => gs[0],
    groupHead: g => {
      const q = qOf(g);
      const words = q && (q.instructions || {}).question;
      return el('div', {class: 'qhead'}, [
        el('div', {class: 'tlab', text: (qByPrefix[g.prefix] || g.prefix) + ' \u00b7 '
          + num(g.rows.length) + ' answers \u00b7 ' + num(g.low) + ' under 0.60'}),
        el('p', {class: 'quote', text: words || 'This question\u2019s wording is not in the '
          + 'bank any more; its answers are kept and served dated.'})]);
    },
    blurb: 'The live answer to every question asked about this project: one row per subject and '
      + 'question, the latest. Grouped by the question that asked it, because thousands of '
      + 'answers sorted by id is a filing cabinet. Below 0.60 nothing is reported as a finding, so the '
      + 'low column is where the model is telling you it cannot tell.',
    rowsOf: g => g.rows,
    /* *** SEVEN COLUMNS, AND ONE OF THEM HELD TWO DIFFERENT KINDS OF THING. ***
       `subject` fell back to the decision's CONTEXT when it could not resolve a model name, and
       a claim-scoped decision's context is the claim's own text -- so one cell in a column of
       model names held 120 characters of prose. Reported from the field as "is that about an
       assay column or water table shit?", which is the right question to ask of a column that
       answers two things.

       The subject is now always the subject, derived from the decision key, with a pill for the
       scope. What it was asked ABOUT has its own column, and the full text is in the pane. */
    /* *** THE SORT KEY HAS TO BE ON THE SCREEN. ***
       Four columns in this pane clipped `confidence` off the right edge -- and the list is
       sorted by it ascending, because the lowest is where the model is telling you it cannot
       tell. A list sorted by a column you cannot see is a list in no apparent order.

       So three columns: what it is about, what it answered, how sure. The subject, the scope and
       the question id are all in the pane, one click away, with room to be read. */
    rowCols: [
      {key: 'about', label: 'about', clip: 1, val: a => a.context || subjectOf(a).name},
      {key: 'a', label: 'answered', clip: 1, val: a => a.answer},
      {key: 'c', label: 'sure', n: 1, val: a => a.confidence, cell: a => conf(a.confidence)},
    ],
    rowSort: 'c', rowDir: 1,
    rowText: a => [a.question, a.key, a.context, a.answer, a.prompt_version].join(' '),
    /* *** THE QUESTION TEXT ABOVE ITS ANSWERS, WHICH IS THE THING BEING MEASURED. ***
       A row read `column_role__what_is_it = dimension, 0.62` and the question it answered was
       somewhere else entirely -- another tab. An answer without its question is a value with no
       unit. */
    detailOf: a => {
      const q = (DATA.questions || []).find(x => a.question.startsWith(x.id_prefix + '__')
                                                 || x.id_prefix === a.question.split('__')[0]);
      const s = subjectOf(a);
      const bits = [
        el('h2', {text: a.answer || '(no answer)'}),
        el('div', {class: 'path', text: a.question}),
      ];
      /* Which thing this answer is ABOUT, linked where it is a model somebody can open. */
      const who = el('div', {class: 'note'});
      who.append(BY_NAME[s.name] ? link(s.name) : el('span', {class: 'mono', text: s.name}));
      if (s.scope) who.append(el('span', {class: 'pill', text: s.scope}));
      bits.push(who);
      if (q && (q.instructions || {}).question)
        bits.push(section('the question it answered',
                          el('p', {class: 'quote', text: q.instructions.question})));
      bits.push(section('about', el('p', {class: 'prose', text: a.context || a.key})));
      bits.push(section('how sure', kv([
        ['confidence', conf(a.confidence)],
        ['next best', a.runner_up ? a.runner_up[0] + ' at ' + a.runner_up[1].toFixed(2)
                                  : 'nothing else scored'],
        ['ruled under', a.prompt_version || 'no recorded version'],
      ])));
      if (a.confidence != null && a.confidence < 0.6)
        bits.push(el('p', {class: 'note', text: 'Under 0.60, so nothing is reported as a finding '
          + 'from this. It is the model saying it cannot tell, which usually means the state it '
          + 'was given does not carry what the question asks for.'}));
      return bits;
    },
  }));
}

/* *** THE THINGS YOU CONFIGURE BY HAND WERE THE ONES DUMPED AS RAW JSON. ***
   The vocabulary, the question text and the per-check policy are hand-written and hand-
   maintained, so they are exactly what a person opens this page to read, and they were the only
   parts rendered as a `<pre>` blob. These render whatever shape the value happens to be. */
function kvAny(v, depth) {
  depth = depth || 0;
  if (v == null || v === '') return el('span', {class: 'tot', text: '—'});
  if (Array.isArray(v)) {
    if (!v.length) return el('span', {class: 'tot', text: 'none'});
    if (v.every(x => typeof x !== 'object'))
      return el('div', {class: 'chips flat'}, v.map(x => el('span', {class: 'chip mono',
        text: String(x)})));
    return el('div', {}, v.map(x => el('div', {class: 'sub'}, [kvAny(x, depth + 1)])));
  }
  if (typeof v === 'object') {
    const d = el('dl', {class: 'kv' + (depth ? ' sub' : '')});
    for (const k of Object.keys(v).sort()) {
      d.append(el('dt', {text: k}));
      d.append(el('dd', {}, [kvAny(v[k], depth + 1)]));
    }
    return d;
  }
  if (typeof v === 'string' && v.length > 90)
    return el('p', {class: 'prose', text: v});
  return el('span', {text: String(v)});
}

/* ---------------------------------------------------------------------------- Questions */
function questionsTab(host) {
  const detail = el('div', {class: 'detail'});
  const byFam = {};
  for (const a of DATA.adjudications) {
    const k = a.family || ''; byFam[k] = byFam[k] || {human: 0, all: 0};
    byFam[k].all++; if (a.source === 'human') byFam[k].human++;
  }
  const asked = {};
  for (const d of DATA.decisions) { const p = d.question.split('__')[0];
    asked[p] = (asked[p] || 0) + 1; }

  const list = grid(DATA.questions, [
    {key: 'name', label: 'family', mono: 1, val: q => q.name},
    {key: 'v', label: 'version', mono: 1, val: q => q.prompt_version},
    {key: 'asked', label: 'asked', n: 1, val: q => asked[q.id_prefix] || 0},
    {key: 'human', label: 'human', n: 1, val: q => (byFam[q.name] || {}).human || 0,
     cell: q => { const n = (byFam[q.name] || {}).human || 0;
       return el('span', {class: n ? 'ok' : 'tot', text: num(n)}); }},
  ], {placeholder: 'filter questions...', scroll: 1, sort: 'name', pick: q => show(q),
      text: q => [q.name, q.id_prefix, q.prompt_version, JSON.stringify(q.instructions)].join(' ')});

  function show(q) {
    const n = (byFam[q.name] || {}).human || 0;
    const d = detail; d.replaceChildren();
    d.append(el('h2', {text: q.name}));
    d.append(el('div', {class: 'path mono',
      text: [q.id_prefix, q.prompt_version, q.kind, q.origin].filter(Boolean).join('  ·  ')}));

    d.append(section('how many verdicts, and whose', kv([
      ['asked on this project', num(asked[q.id_prefix] || 0) + ' subject(s)'],
      ['human verdicts', el('span', {class: 'pill ' + (n ? 'on' : 'bad'), text: String(n)})],
      ['all verdicts', String((byFam[q.name] || {}).all || 0)],
    ])));
    /* *** IT EXPLAINED THE DIFFERENCE BETWEEN TWO NUMBERS THAT WERE BOTH ZERO. ***
       This paragraph printed on every card, including one reading `human 0, all 0`, where the
       distinction it draws has nothing to draw it between. It is a fact about the tab, so it
       lives in the tab header; here it appears only when a card actually holds both kinds. */
    const allV = (byFam[q.name] || {}).all || 0;
    if (allV > n)
      d.append(el('p', {class: 'note', text: (allV - n) + ' of these came from an agent. '
        + 'They are evidence, not authority: they cannot gate a build or count toward '
        + 'min_adjudications.'}));

    /* The question as it is SENT, as prose rather than as a JSON object. */
    const ins = q.instructions || {};
    const qs = el('div');
    if (ins.question) qs.append(el('p', {class: 'quote', text: ins.question}));
    for (const k of Object.keys(ins).sort()) {
      if (k === 'question') continue;
      qs.append(el('p', {class: 'note'}, [el('b', {text: k + ': '}),
                                          el('span', {text: String(ins[k])})]));
    }
    d.append(section('what it asks', qs));

    /* Every option it may return, each one a block: the name, what it means, what it is NOT for,
       and the examples. This is the text `effectiveness` measures agreement against, so it is
       the text a person needs when a version number moves. */
    const crit = q.criteria || {};
    const opts = el('div');
    for (const name of Object.keys(crit).sort()) {
      const c = crit[name], blk = el('div', {class: 'opt'});
      blk.append(el('div', {class: 'optname mono', text: name}));
      if (typeof c === 'string') { blk.append(el('p', {class: 'prose', text: c})); }
      else {
        if (c.what) blk.append(el('p', {class: 'prose', text: c.what}));
        if (c.not_for) blk.append(el('p', {class: 'note'},
          [el('b', {text: 'not for: '}), el('span', {text: String(c.not_for)})]));
        for (const k of Object.keys(c).sort()) {
          if (k === 'what' || k === 'not_for' || k === 'examples') continue;
          blk.append(el('p', {class: 'note'},
            [el('b', {text: k + ': '}), el('span', {text: String(c[k])})]));
        }
        if ((c.examples || []).length)
          blk.append(el('div', {class: 'chips flat'},
            c.examples.map(x => el('span', {class: 'chip mono', text: String(x)}))));
      }
      opts.append(blk);
    }
    d.append(section('the ' + Object.keys(crit).length + ' answers it may give', opts));
  }

  host.replaceChildren(
    el('p', {class: 'note', text: 'Every question assay will ask, in full, beside how often it has been ruled on. '
             + 'Only a human verdict can gate a build.'}),
    el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a question to read its instructions and every option, exactly as they are sent.'}));
  const first = $('tbody tr', list); if (first) first.click();
}

/* ------------------------------------------------------------------------------- Config */
function configTab(host) {
  const c = DATA.config || {}, bits = [];
  /* The one tab besides the Overview that carries a plate: it has a short opening line and a
     long table under it, so a cut set into that line costs nothing and fills paper that was
     otherwise empty. */
  /* *** A FLOAT INSIDE A flow-root PARAGRAPH IS NOT A FLOAT, IT IS A BLOCK. ***
     Containing it in the opening line meant one short sentence wrapped around a 172px plate and
     everything after it started below the whole thing -- a column of bare paper down the left
     and the settings pushed half a screen down. It sits in the TAB, on the right, and the note
     and the resolved settings run up the left of it. */
  const cut = (DATA.cuts || {}).tower;
  if (cut) bits.push(el('img', {class: 'cut tabcut', src: cut, alt: ''}));
  bits.push(el('p', {class: 'note tabhead', text:
    'What was actually resolved, which is not always what the file says. Everything under here '
    + 'you wrote by hand.'}));

  const scalars = Object.entries(c).filter(([, v]) => typeof v !== 'object' || v === null);
  if (scalars.length) bits.push(section('resolved', kv(scalars.map(([k, v]) => [k, String(v)]))));

  /* *** THE VOCABULARY IS THE POINT OF THE WHOLE CONFIG AND IT WAS A JSON BLOB. ***
     Fifteen terms written once made `traverse` flag wdid joins without anyone writing a water
     question. Knowledge written once reaching questions nobody wrote is the promise, so it gets
     a table you can read rather than a pre block you skim past. */
  if (c.vocab && Object.keys(c.vocab).length) {
    const rows = Object.keys(c.vocab).sort().map(k => ({term: k, def: c.vocab[k]}));
    bits.push(section('vocabulary (' + rows.length + ' term(s), sent with every question)',
      grid(rows, [
        {key: 'term', label: 'term', mono: 1, val: r => r.term},
        {key: 'def', label: 'what it means here', val: r => JSON.stringify(r.def),
         cell: r => kvAny(r.def)},
      ], {placeholder: 'filter terms...', cap: 400})));
  }

  if (c.questions && Object.keys(c.questions).length) {
    const rows = Object.keys(c.questions).sort().map(k => ({q: k, v: c.questions[k]}));
    bits.push(section('per-check policy (' + rows.length + ')', grid(rows, [
      {key: 'q', label: 'check', mono: 1, val: r => r.q},
      {key: 'v', label: 'configured', val: r => JSON.stringify(r.v), cell: r => kvAny(r.v)},
    ], {placeholder: 'filter...', cap: 400})));
  }

  if (c.waivers && Object.keys(c.waivers).length) {
    const rows = [];
    for (const m of Object.keys(c.waivers).sort())
      for (const w of [].concat(c.waivers[m])) rows.push({model: m, w: w});
    bits.push(section('waivers (' + rows.length + ')', grid(rows, [
      {key: 'model', label: 'model, or a named waiver', mono: 1, val: r => r.model,
       cell: r => link(r.model)},
      {key: 'w', label: 'waived, and why', val: r => JSON.stringify(r.w), cell: r => kvAny(r.w)},
    ], {placeholder: 'filter waivers...', cap: 400})));
    bits.push(el('p', {class: 'note', text: 'A waived finding never reaches the findings table, '
      + 'so nothing above counts it. A waiver whose justification is "looks fine" is how a real '
      + 'finding gets silenced, which is why the reason is required and is shown here.'}));
  }

  for (const k of ['practices', 'explanations']) {
    if (c[k] && Object.keys(c[k]).length) bits.push(section(k, kvAny(c[k])));
  }

  /* A full-width table beside a float is a squeezed table, so the float ends before one. */
  bits.push(el('div', {class: 'clearcut'}));
  if (DATA.runs.length) bits.push(section('runs recorded (' + DATA.runs.length + ')',
    grid(DATA.runs, [
      {key: 'run', label: 'run', mono: 1, val: r => r.run_id},
      {key: 'when', label: 'started', mono: 1, val: r => r.started_at || ''},
      {key: 'av', label: 'assay', mono: 1, val: r => r.assay_version},
      {key: 'dv', label: 'dbt', mono: 1, val: r => r.dbt_version},
      {key: 'm', label: 'models', n: 1, val: r => r.models},
      {key: 'ok', label: 'readable', n: 1, val: r => r.readable},
      {key: 'no', label: 'unreadable', n: 1, val: r => r.unreadable,
       cell: r => el('span', {class: r.unreadable ? 'bad' : 'tot', text: num(r.unreadable)})},
    ], {placeholder: 'filter runs...', sort: 'when', dir: -1})));

  if (DATA.unreadable.length) {
    bits.push(section('what assay could NOT read (' + DATA.unreadable.length + ')',
      grid(DATA.unreadable, [
        {key: 'name', label: 'model', mono: 1, val: u => u.name},
        {key: 'path', label: 'path', mono: 1, val: u => u.path},
        {key: 'why', label: 'why', val: u => u.why},
      ], {placeholder: 'filter...', cap: 500})));
    bits.push(el('p', {class: 'note', text: 'A model absent from every table in this file because '
      + 'its SQL would not parse looks identical, from outside, to a model with nothing wrong '
      + 'with it. That is why it is named here. An absent audit is never a pass.'}));
  }
  host.replaceChildren(...bits);
}

/* ------------------------------------------------------------------------------ Overview

   *** THE FORM COMES FROM THE DATA'S JOB, AND SOMETIMES THE ANSWER IS NOT A CHART. ***
   The ruled-on figure is a hero number: one value, no comparison, and a bar of it would be a bar
   of one. Grain by evidence is a composition of a known whole -> one stacked bar. Findings by
   check is a ranking -> horizontal bars, one hue, because the bars are the same KIND of thing and
   coloring them differently would encode rank as identity.

   *** COLOR LAST, AND COMPUTED. ***
   The page's own provenance pills (green/blue/amber) FAILED the validator as a chart palette:
   green vs blue measure dE 14.1 for normal vision, under the 15 floor. Fine as small text beside
   a word, genuinely hard to separate as adjacent bars. They are not reused here.

   Grain is ORDINAL -- declared beats derived beats judged beats nothing -- so it is one hue,
   dark to light, which encodes the ordering in the ink instead of asking you to learn a key.
   Checked monotonic in OKLab lightness: .433 / .575 / .764, then a neutral for the absence.
   Status colors are reserved, never reused as a series, and always carry their label, because
   `warning` is sub-3:1 against this surface by design. */
/* *** THE ONLY COLOUR ON THE PAGE, AND IT ALWAYS MEANS A QUANTITY. ***
   Forge tones: iron for the strongest evidence, through rust and ember, to a bare rule for what
   nothing settles. One hue family, ordered dark to light, so a stacked bar reads as a scale
   rather than as four unrelated categories -- and so nothing else on the page can be coloured
   without immediately looking like a measurement. */
const RAMP = {declared: '#4a443d', derived: '#a8491a', judged: '#d2833a', none: '#cec5b6'};
const ACT = {fail: '#a8491a', queue: '#d2833a', annotate: '#615a52', waived: '#cec5b6'};
const INK = '#1a1714';
/* A ranked bar is a quantity, so it takes the strongest forge tone rather than the ink: a row of
   near-black bars reads as a block of type rather than as a measure. */
const BAR = RAMP.derived;

/* A composition of a known whole. HTML, not SVG.
   *** AN SVG BAR THAT FILLS ITS CONTAINER NEEDS preserveAspectRatio="none", WHICH STRETCHES THE
   TEXT INSIDE IT. *** A stacked bar is boxes in a row; flex does that natively at any width with
   no distortion and crisp labels, so the only reason to reach for SVG here would be habit.
   2px gaps between segments, and a direct label only where one fits. */
function stackedBar(parts, total) {
  const row = el('div', {class: 'sbar'});
  for (const p of parts) {
    if (!p.n) continue;
    const pct_ = (p.n / total) * 100;
    const seg = el('div', {class: 'sseg', style: `flex:0 0 calc(${pct_}% - 2px);background:${p.color}`,
                           title: `${p.label}: ${num(p.n)} of ${num(total)} (${Math.round(pct_)}%)`});
    // Selective, never a number on every segment: a 1%-wide slice cannot hold one legibly.
    if (pct_ > 7) seg.append(el('span', {class: 'sval', text: num(p.n)}));
    row.append(seg);
  }
  return row;
}

/* A ranking. One hue: the bars are the same KIND of thing, so color would encode rank. */
/* A ranking. Also HTML: the label gutter has to fit the longest NAME, and three check names are
   over 200px at 12px monospace -- `description_contradicts_the_code` is 230px. In SVG that is a
   number to guess and get wrong; in a grid the column measures itself. One hue, because the bars
   are the same KIND of thing and coloring them apart would encode rank as identity. */
function rankedBars(rows) {
  const max = Math.max(...rows.map(r => r.n), 1);
  const host = el('div', {class: 'rank'});
  for (const r of rows) {
    const line = el('div', {class: 'rrow' + (r.onclick ? ' clk' : ''),
                            title: r.tip || `${r.label}: ${num(r.n)}`});
    const lab = el('span', {class: 'rlab mono', text: r.label});
    /* A qualifier belongs under the name, not inside it: `arbitrary_pick assay.0.11.0` on one
       line reads as a single identifier and the version looks like part of the check. */
    if (r.sub) lab.append(el('span', {class: 'rsub', text: r.sub}));
    line.append(lab);
    const track = el('span', {class: 'rtrack'});
    track.append(el('span', {class: 'rfill',
                             style: `width:${Math.max(1.5, (r.n / max) * 100)}%;`
                                    + `background:${r.color || BAR}`}));
    line.append(track);
    /* *** `114` AND `6 read` RENDERED AS `114 6 read`, WHICH READS AS 1,146. ***
       Two numbers separated by whitespace are one number to a reader. The check dropdown already
       uses this separator; a second spelling of the same idea is how they drift apart. */
    line.append(el('span', {class: 'rval', text: num(r.n) + (r.note ? ' · ' + r.note : '')}));
    if (r.onclick) line.onclick = r.onclick;
    host.append(line);
  }
  return host;
}

function tile(big, label, note, cls) {
  return el('div', {class: 'tile'}, [
    el('div', {class: 'tilebig ' + (cls || ''), text: big}),
    el('div', {class: 'tilelab', text: label}),
    el('div', {class: 'tilenote', text: note || ''}),
  ]);
}

function block(title, note, node) {
  const b = el('div', {class: 'ovblock'});
  b.append(el('h3', {text: title}));
  if (note) b.append(el('p', {class: 'note', text: note}));
  if (node) b.append(node);
  return b;
}

/* ---------------------------------------------------------------------- What to configure

   *** THE FINDINGS AND THE CONFIG WERE TWO TABS THAT NEVER REFERRED TO EACH OTHER. ***
   One said 257 things are wrong. The other showed the YAML that decides which of them matter. The
   step between -- "so write THIS" -- was left to the reader, and on this page that gap looked
   exactly like a gap in the tool. Now it is a tab, and every row in it carries the measurement
   that produced it, so a reader can disagree with the evidence rather than with the proposal.

   *** EVERY `means:` AND `implies:` IS EMPTY, ON THE PAGE TOO. ***
   Not an oversight and not something the page should helpfully fill from the model name. A
   plausible definition written here would look exactly like a definition somebody decided on,
   and would then travel with every judged question from that point on. */
function suggestTab(host) {
  const S = DATA.suggestions || [];

  if (!S.length) {
    /* An empty list is not a complete config, and the two must not read alike. */
    host.replaceChildren(section('nothing to suggest', el('p', {class: 'note', text:
      'No rule found a candidate. That is not the same as the config being complete: each rule '
      + 'needs its own evidence, and most of it is written during `assay check` and `assay probe`. '
      + 'A store with no rulings in it cannot propose a waiver, and says so rather than implying '
      + 'there is nothing to waive.'})));
    return;
  }

  /* *** 113 CARDS OF EQUAL WEIGHT IN ONE COLUMN IS NOT A REPORT. ***
     "i gotta scroll for 100 fucking years because of how badly made that UI is." Forty-four of
     them read `X is 99.68% unique in Y and is named like a key` with an identical empty YAML
     block underneath, which is repetition doing no work: the rows are the same shape, so they
     are a TABLE, and what differs between groups is the REASON they fired.

     So the left pane lists the reasons with a count each, and picking one lists its rows. The
     draft YAML -- the thing you came to copy -- is in the right pane, once, for the row you
     picked, rather than a hundred times down the page. */
  const LABEL = {open: 'Decide first', vocab: 'Vocabulary', questions: 'Per-check policy',
                 waivers: 'Waivers', explanations: 'Row explanations',
                 descriptions: 'Column descriptions'};
  const SECT = ['open', 'vocab', 'questions', 'waivers', 'explanations', 'descriptions'];
  const byBasis = {};
  for (const r of S) {
    const k = (r.section || '') + '|' + (r.basis || '');
    const g = byBasis[k] = byBasis[k] || {section: r.section, basis: r.basis || '(no rule)',
                                          rows: [], decide: r.decide || ''};
    g.rows.push(r);
    if (r.decide && !g.decide) g.decide = r.decide;
  }
  const groups = Object.values(byBasis).sort((a, b) =>
    (SECT.indexOf(a.section) - SECT.indexOf(b.section)) || b.rows.length - a.rows.length);

  host.replaceChildren(drill({
    noun: 'candidates', groups: groups, rowFilter: 'filter candidates...',
    chip: g => g.basis, keepOrder: 1, groupFilter: 'find a reason...',
    sectionOf: g => LABEL[g.section] || g.section,
    blurb: 'assay measured these and writes no meaning. A means: in a draft is either blank or '
      + 'QUOTED from a sentence this project already uses, with where it came from; a column '
      + 'description draft says per line whether it is quoted or built from recorded facts. '
      + 'Grouped by the reason each one fired, because forty rows of one reason are one decision.',
    rowsOf: g => g.rows.slice().sort((a, b) => (b.rank || 0) - (a.rank || 0)),
    /* *** THE HEADLINE IS THE GROUP'S OWN REASON, REPEATED ONCE PER ROW. ***
       123 rows reading "`X` is described identically in N models and the vocab does not carry
       it" is the reason spelled 123 times: it is already the crumb above the table and the first
       line of the pane. What differs between rows is the NAME and the number, so those are the
       columns. */
    /* `what was measured` was empty on every row of the largest rule, because that rule keeps
       its evidence on the RULE rather than repeating it per row. A column that is blank
       everywhere is furniture. What varies is the candidate, which file it would go in, and
       where it ranks within its own rule. */
    rowCols: [
      {key: 'k', label: 'candidate', mono: 1, val: r => r.key || r.headline},
      {key: 'sec', label: 'edit', val: r => LABEL[r.section] || r.section,
       cell: r => el('span', {class: 'pill', text: LABEL[r.section] || r.section})},
      {key: 'rank', label: 'rank', n: 1, val: r => r.rank},
    ],
    rowSort: 'rank', rowDir: -1,
    rowText: r => [r.headline, r.key, (r.measured || []).join(' ')].join(' '),
    detailOf: (r, g) => {
      const bits = [el('h2', {text: r.key || r.headline})];
      bits.push(el('div', {class: 'path mono', text: (LABEL[r.section] || r.section)
                                                     + '  \u00b7  ' + (r.basis || '')}));
      bits.push(el('p', {class: 'prose', text: r.headline}));
      if (r.measured && r.measured.length)
        bits.push(section('what was measured',
          el('ul', {class: 'sug-m'}, r.measured.map(m => el('li', {text: m})))));
      /* The refusal is a property of the RULE, so it is shown with the rule and not repeated on
         every one of its rows. Eight copies of one sentence read as noise. */
      if ((g && g.decide) || r.decide)
        bits.push(section('assay will not pick between these',
          el('div', {class: 'sug-d', text: (g && g.decide) || r.decide})));
      if (r.draft) bits.push(section(r.section === 'descriptions'
        ? 'a draft for schema.yml -- edit it first' : 'paste this into audit.yml',
        el('pre', {class: 'sug-y', text: r.draft})));
      else bits.push(el('p', {class: 'note', text: 'No draft: there is nothing to paste until '
        + 'the question above is answered.'}));
      return bits;
    },
  }));
}

function goTab(name, label) {
  const a = el('a', {class: 'lk', href: '#', text: label});
  a.onclick = e => { e.preventDefault(); open(name); };
  return a;
}

function understoodTab(host) {
  const M_ = DATA.models, F = DATA.findings, meta = DATA.meta;
  const bits = [];

  /* *** A ZERO THAT MEANS "NOTHING HAS RUN" LOOKS EXACTLY LIKE ONE THAT MEANS "NOTHING IS
     WRONG", AND ONLY ONE OF THEM IS GOOD NEWS. ***
     Reported from the field as the most dangerous behaviour of the whole run: a store created at
     a path a container could not see reported `0 of 76 model(s) ruled`, with no error anywhere. */
  if (meta.new_store)
    bits.push(el('div', {class: 'newstore'}, [
      el('b', {text: 'Nothing has been recorded yet. '}),
      el('span', {text: meta.new_store})]));

  /* *** "474" IS NOT A SENTENCE AND "FOUND 474 WHAT" IS THE RIGHT QUESTION. ***
     The first screen used three floating numbers with their nouns in a caption underneath, so
     the biggest type on the page said nothing on its own. It is an assay ticket now: what was
     CHARGED, what was ASSAYED out of it, and what it COST -- three columns of an account, each
     number carrying its unit on the same line, ruled the way a ticket is ruled.

     The loop number stays one block down. It is the honest measure and it is not the argument. */
  const ruledN = F.filter(f => f.ruled_finding).length;
  const agentN = DATA.adjudications.filter(a => a.source === 'agent').length;
  const humanN = DATA.adjudications.filter(a => a.source === 'human').length;
  const checks = new Set(F.map(f => f.check)).size;
  const classified = DATA.claims.filter(c => c.kind).length;
  const spent = (DATA.cost || {}).usd;

  function column(label, n, unit, sub) {
    const c = el('div', {class: 'tcol'});
    c.append(el('div', {class: 'tlab', text: label}));
    c.append(el('div', {}, [el('span', {class: 'tnum', text: n}),
                            el('span', {class: 'tunit', text: unit})]));
    c.append(el('div', {class: 'tsub', text: sub}));
    return c;
  }

  /* *** LEAD WITH WHAT NOTHING ELSE COULD HAVE DONE, NOT WITH AN INVENTORY. ***
     "charged: 356 models" opened the page, which is a count anyone's catalog already has. The
     argument is the reading: the project's own prose read and classified, questions answered that
     no parser can answer, and what it cost -- and then what it found, which no dbt test could have
     expressed. One sentence says it, and the four columns are its ledger. */
  const answered = DATA.decisions.length;
  const leadBits = [];
  if (DATA.claims.length)
    leadBits.push(num(DATA.claims.length) + ' sentences of this project\u2019s own prose read'
                  + (classified === DATA.claims.length ? ' and classified'
                     : classified ? ' and ' + num(classified) + ' classified' : ''));
  if (answered)
    leadBits.push(num(answered) + ' questions answered across ' + num(meta.models) + ' models');
  const leadCost = spent == null ? '' : ' for $' + spent.toFixed(2);
  bits.push(el('p', {class: 'tlead', text: (leadBits.length
    ? leadBits.join(', and ') + leadCost + ', finding '
    : 'Reading ' + num(meta.models) + ' models found ')
    + num(F.length) + ' defects no dbt test can express.'}));

  const ticket = el('div', {class: 'ticket'}, [
    column('read', num(DATA.claims.length), 'sentences',
           (classified === DATA.claims.length ? 'Every one' : num(classified))
           + ' classified by what job it does; ' + num(meta.models)
           + ' models and ' + num(DATA.edges.length) + ' hops parsed.'),
    column('judged', num(answered), 'answers',
           'Questions no parser can settle -- what a filter is for, what a NULL means -- each '
           + 'answer stored with what it was asked from.'),
    column('found', num(F.length), 'defects',
           'Across ' + num(checks) + ' assays: grain, meaning, provenance and drift, which no '
           + 'unique or not_null can say.'),
    column('at cost', spent == null ? '\u2014' : '$' + spent.toFixed(2), '',
           spent == null
             ? 'This store predates the ledger, so what it cost is unknown rather than nothing.'
             : 'Every call recorded, one row each. The Spend tab has the ledger.'),
  ]);
  /* The plate earns its place by being the thing the page is named after: a charge going into a
     furnace and something being drawn off it. */
  const cut = (DATA.cuts || {}).condensers;
  bits.push(el('div', {class: 'ticketwrap'}, [
    ticket,
    cut ? el('img', {class: 'cut ticketcut', src: cut, alt: ''}) : el('div'),
  ]));

  // ---- the loop number. It moves only when a person reads SQL, which is why it is not the hero.
  const unread = meta.models - (meta.coverage || {}).readable;
  bits.push(block('How much of it a person has actually read',
    'The one number no release can move. Agent rulings triage what to read first and gate '
    + 'nothing.',
    el('div', {class: 'tiles'}, [
      tile(num(ruledN) + ' of ' + num(F.length), 'findings ruled on',
           humanN + ' human verdict(s), ' + agentN + ' agent', ruledN ? '' : 'bad'),
      tile(num((meta.coverage || {}).readable || 0), 'assay could read',
           unread ? num(unread) + ' it could not, and that is not a pass' : 'all of them',
           unread ? 'bad' : ''),
      tile(num(DATA.claims.filter(c => c.contradicted != null).length), 'claims the code contradicts',
           'of ' + num(DATA.claims.length) + ' extracted'),
      tile(num(DATA.questions.length), 'question families', num(meta.sources) + ' sources read'),
    ])));

  // ---- grain: a composition of a known whole, ordered by evidence, so one hue dark->light
  const g = {declared: 0, derived: 0, judged: 0, none: 0};
  for (const m of M_) g[((m.grain || {}).source) || 'none'] = (g[((m.grain || {}).source) || 'none'] || 0) + 1;
  const gparts = [
    {label: 'declared by a test', n: g.declared, color: RAMP.declared},
    {label: 'derived from the SQL', n: g.derived, color: RAMP.derived},
    {label: 'judged', n: g.judged, color: RAMP.judged},
    {label: 'nothing settles it', n: g.none, color: RAMP.none},
  ];
  const glegend = el('div', {class: 'legend'}, gparts.map(p => el('span', {class: 'lgi'}, [
    el('span', {class: 'sw', style: 'background:' + p.color}),
    el('span', {text: p.label + ' ' + num(p.n)})])));
  bits.push(block('What is one row of this?',
    'Strongest evidence is darkest. Never summed: a declared grain and a judged one are '
    + 'different facts.',
    el('div', {}, [stackedBar(gparts, M_.length), glegend])));

  // ---- findings by check, ranked, one hue
  const byCheck = {};
  for (const f of F) byCheck[f.check] = (byCheck[f.check] || 0) + 1;
  const rows = Object.entries(byCheck).sort((a, b) => b[1] - a[1]).map(([c, n]) => {
    const mine = F.filter(f => f.check === c);
    const read = mine.filter(f => f.ruled_finding).length;
    return {label: c, n: n, note: read ? read + ' read' : '',
            tip: `${c}: ${n} finding(s), ${read} read by a person, worst reach `
                 + Math.max(...mine.map(f => f.marts)) + ' marts',
            onclick: () => { open('findings'); }};
  });
  bits.push(block('What is wrong, and how much of it',
    'Ranked by count. Click a bar for the findings.',
    rankedBars(rows)));

  // ---- what would happen on a build. STATUS colors, always with their label.
  const acts = {fail: 0, queue: 0, annotate: 0, waived: 0};
  for (const f of F) if (acts[f.action] != null) acts[f.action]++;
  const aparts = [
    {label: 'would FAIL the build', n: acts.fail, color: ACT.fail},
    {label: 'queued for a person', n: acts.queue, color: ACT.queue},
    {label: 'annotated only', n: acts.annotate, color: ACT.annotate},
    {label: 'waived, with a reason', n: acts.waived, color: ACT.waived},
  ];
  const alegend = el('div', {class: 'legend'}, aparts.filter(p => p.n).map(p =>
    el('span', {class: 'lgi'}, [el('span', {class: 'sw', style: 'background:' + p.color}),
                                el('span', {text: p.label + ' ' + num(p.n)})])));
  bits.push(block('What this would do to a build',
    'Your audit.yml, applied. Nothing gates before it has verdicts.',
    el('div', {}, [stackedBar(aparts.filter(p => p.n), F.length), alegend])));

  // ---- the configuration gap
  /* *** TWENTY BARS, ALL THE SAME LENGTH, NONE OF THEM LABELLED. ***
     `rankedBars` was handed rows with no `label` and no `n`, so it drew a full-width track for
     every row and put the check's name in a hover title -- on a page whose whole argument is
     that a reader should not have to ask. "wtf even is this visualization here?" was the right
     question: there was no magnitude in it at all.

     The magnitude that belongs here is how many findings each unnamed check is firing, which is
     the number that decides whether configuring it matters. Same source as the ranking above. */
  const gap = DATA.unconfigured || [];
  if (gap.length) {
    const grows = gap.map(u => ({
      label: u.check,
      n: byCheck[u.check] || 0,
      note: u.shipped ? 'assay suggests ' + u.shipped
                      : 'not configured \u00b7 warns, cannot fail a build',
      color: RAMP.none,
      tip: `${u.check} is firing ${num(byCheck[u.check] || 0)} finding(s) and audit.yml does not `
           + `name it`,
      onclick: () => { open('suggest'); }}))
      .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
    bits.push(block(gap.length + ' check(s) fired that your audit.yml does not name',
      'The bar is how many findings each one is firing. Unconfigured checks fall back to their '
      + 'severity and cannot fail a build. Click one for what to write.',
      rankedBars(grows)));
  }

  /* ---- did the questions get better?
     *** THE RECORD USED TO SIT HERE IN AN IFRAME, AND IT WAS THE SAME NUMBERS TWICE. ***
     Its hero IS this page's hero; its findings-by-check IS the ranking above. Two sections were
     the reason it was still bolted on, so they are native now and the frame is gone. The record
     still exists -- `assay page --plain` writes it, `record.html` carries it in the artifact --
     it is just not duplicated inside the page that replaced it. */
  const eff = (DATA.effectiveness || []).filter(r => r.n);
  if (eff.length) {
    const bySrc = {};
    for (const r of eff) (bySrc[r.source] = bySrc[r.source] || []).push(r);
    const wrap = el('div');
    for (const src of ['human', 'label', 'agent'].filter(s_ => bySrc[s_])) {
      wrap.append(el('p', {class: 'srclab', text: src === 'human'
        ? 'human \u00b7 the only kind that gates anything'
        : src === 'label' ? 'label \u00b7 derived from your own tests, evidence not truth'
        : 'agent \u00b7 triage, counted toward nothing'}));
      /* *** THE VERSION IS THE ONE THAT WAS RULED UNDER, AND THE BAR DID NOT SAY SO. ***
         `arbitrary_pick assay.0.11.0` reads as "assay is running something old". It is not: it
         is the assay that was live when that verdict was made, which is exactly what a
         before-and-after comparison needs. The data was right and the label was the lie, so the
         version moves out of the bar label and into the note that explains it. */
      wrap.append(rankedBars(bySrc[src].map(r => ({
        sub: r.prompt_version ? 'ruled under ' + r.prompt_version : 'unversioned',
        n: r.n,
        color: r.agreement == null ? RAMP.none
             : r.agreement >= 0.8 ? RAMP.declared
             : r.agreement >= 0.5 ? RAMP.derived : RAMP.judged,
        note: (r.agreement == null ? '' : Math.round(r.agreement * 100) + '% agreed')
              + (r.unclear ? '  \u00b7 ' + r.unclear + ' unclear' : '')
              + (r.open_disagreements ? '  \u00b7 ' + r.open_disagreements + ' open' : ''),
        tip: `${r.family}, ruled under ${r.prompt_version || 'no recorded version'}: `
             + `${r.n} ruled, `
             + `${r.agreement == null ? 'no rate' : Math.round(r.agreement * 100) + '% agreed'}, `
             + `${r.unclear} unclear, ${r.open_disagreements} open disagreement(s)`,
      }))));
    }
    bits.push(block('Did the questions get better?',
      'Per family, per version. Unclear is excluded: wrong criteria and a thin state need '
      + 'different fixes.', wrap));
  }

  // ---- what moved
  const mv = DATA.moved || {};
  if (mv.same != null) {
    bits.push(block('What moved since the previous run',
      'Appeared, went, or stayed. Gone can mean fixed, or no longer seen.',
      el('div', {class: 'tiles'}, [
        tile(num(mv.n_new || 0), 'appeared', 'since the previous recorded run',
             (mv.n_new || 0) ? 'bad' : ''),
        tile(num(mv.n_gone || 0), 'went away', 'fixed, or no longer seen'),
        tile(num(mv.same || 0), 'unchanged', 'still true, and still unread unless ruled'),
      ])));
  } else {
    bits.push(block('What moved since the previous run',
      'Only one run is recorded, so nothing can have moved yet -- which is different from '
      + 'nothing having moved. Run `assay check` again after your next change.', null));
  }

  host.replaceChildren(...bits);
}

/* ------------------------------------------------------------------------------- spend

   *** THE PAGE HELD EVERY ANSWER AND NO DOLLAR FIGURE. ***
   "Is this worth running" had to be asked at a terminal, against a different command, while the
   thing it bought was sitting in the next tab. Every number here is one row per CALL out of
   `model_calls`: a batch of eight questions about one state is ONE call and eight answers, so
   totalling the answer rows counts it eight times -- $4.25 on a store that spent $1.32.
*/
/* *** A DAY WITH RUNS AND NO SPEND IS A ZERO, NOT AN ABSENCE. ***
   The tab used to render `by_day` straight, so a day nothing was spent on simply was not there --
   indistinguishable from broken recording. Every day that RAN gets a column, and a zero column
   carries the reason it is zero. */
function daySeries(days, pick, fmt) {
  const max = Math.max(...days.map(d => pick(d) || 0), 0);
  const host = el('div', {class: 'days'});
  for (const d of days.slice(0, 30).slice().reverse()) {
    const v = pick(d) || 0;
    const col = el('div', {class: 'day' + (v ? '' : ' zero'),
                           title: d.day + ' · ' + fmt(d)
                                  + (d.why ? '\n' + d.why : '')});
    const track = el('div', {class: 'daytrack'});
    track.append(el('div', {class: 'dayfill',
                            style: `height:${max ? Math.max(2, (v / max) * 100) : 2}%`}));
    col.append(track);
    col.append(el('div', {class: 'daylab', text: d.day.slice(5)}));
    host.append(col);
  }
  return host;
}

function spendTab(host) {
  const c = DATA.cost || {};
  const bits = [];
  if (!c.calls && !((c.warehouse || {}).calls)) {
    /* An absent ledger is not a free project. This store predates `model_calls`, or nothing has
       been asked here -- two different facts, and neither of them is a zero. */
    bits.push(block('No ledger in this store',
      DATA.decisions && DATA.decisions.length
        ? 'This store holds ' + num(DATA.decisions.length) + ' answers and no record of the calls '
          + 'that produced them: they were decided before assay recorded one. What they cost is '
          + 'not zero, it is unknown, and the page will not print a number for it.'
        : 'Nothing has been asked against this project yet.'));
    host.replaceChildren(...bits);
    return;
  }
  const money = n => '$' + (n == null ? '--' : n < 1 ? n.toFixed(4) : n.toFixed(2));
  const wh = c.warehouse || {};
  bits.push(el('p', {class: 'note', text: 'What the thinking cost and what the warehouse cost, kept apart because '
                     + 'they are priced by different people in different units.'}));
  bits.push(block('What this project has cost', null, el('div', {class: 'tiles'}, [
    tile(money(c.usd || 0), 'thinking', num(c.calls || 0) + ' model call(s)'),
    tile(wh.calls ? money(wh.usd) : '--', 'the warehouse',
         wh.calls ? num(wh.calls) + ' statement(s), ' + bytes(wh.bytes_estimated)
                  : 'no statement recorded'),
    tile(num(c.input_tokens || 0), 'input tokens', 'output is shown and never priced'),
  ])));

  const days = c.days || [];
  if (days.length) {
    const spice = el('div');
    spice.append(el('p', {class: 'srclab', text: 'model spend per day'}));
    spice.append(daySeries(days, d => d.usd, d => money(d.usd) + ' · '
                                                 + num(d.calls) + ' call(s)'));
    if (wh.calls) {
      spice.append(el('p', {class: 'srclab', text: 'warehouse statements per day'}));
      spice.append(daySeries(days, d => d.warehouse_calls,
                             d => num(d.warehouse_calls) + ' statement(s), '
                                  + bytes(d.warehouse_bytes)));
    }
    bits.push(block('Per day', 'A day that ran and spent nothing is a ZERO with the reason '
      + 'beside it, never a missing column.',
      el('div', {}, [spice, grid(days, [
        {key: 'day', label: 'day', mono: 1, val: d => d.day},
        {key: 'runs', label: 'runs', n: 1, val: d => d.runs},
        {key: 'calls', label: 'calls', n: 1, val: d => d.calls},
        {key: 'usd', label: 'usd', n: 1, val: d => d.usd,
         cell: d => el('span', {text: money(d.usd)})},
        {key: 'wh', label: 'statements', n: 1, val: d => d.warehouse_calls},
        {key: 'why', label: 'why', val: d => d.why},
      ], {sort: 'day'})])));
  }

  const cols = [
    {key: 'k', label: '', mono: 1, val: r => r[0]},
    {key: 'calls', label: 'calls', n: 1, val: r => r[1]},
    {key: 'tok', label: 'input tokens', n: 1, val: r => r[2]},
    {key: 'usd', label: 'usd', n: 1, val: r => r[3], cell: r => el('span', {text: money(r[3])})},
  ];
  for (const [title, rows] of [['by caller', c.by_caller], ['by question family', c.by_family]]) {
    if (rows && rows.length) bits.push(block(title, null, grid(rows, cols, {sort: 'usd'})));
  }
  if (wh.calls) {
    /* One table for what assay spent on thinking, one for what it spent on the warehouse. They
       are priced by different people in different units, and a merged number would be two
       facts sharing one name. */
    const wcols = [
      {key: 'k', label: '', mono: 1, val: r => r[0]},
      {key: 'n', label: 'statements', n: 1, val: r => r[1]},
      {key: 'by', label: 'scanned', n: 1, val: r => r[2],
       cell: r => el('span', {text: bytes(r[2])})},
      {key: 'usd', label: 'usd', n: 1, val: r => r[3], cell: r => el('span', {text: money(r[3])})},
      {key: 'ms', label: 'time', n: 1, val: r => r[4],
       cell: r => el('span', {text: (r[4] / 1000).toFixed(1) + 's'})},
      {key: 'bad', label: 'failed', n: 1, val: r => r[5]},
    ];
    bits.push(block('The warehouse, by caller',
      'Every statement assay sends goes out through YOUR dbt, so this is what it asked your '
      + 'warehouse to do. A failed statement is counted and kept out of the money.',
      grid(wh.by_caller || [], wcols, {sort: 'by'})));
  }
  /* *** WHAT THIS TOTAL DOES NOT COVER, ON THE PAGE AND NOT ONLY IN THE TERMINAL. *** */
  const notes = [];
  if (c.calls_without_usage)
    notes.push(num(c.calls_without_usage) + ' call(s) returned no usage and are not in this '
      + 'total. Not estimated: an absent measurement is not a zero.');
  const recon = (c.id_source || {}).reconstructed || 0;
  if (recon)
    notes.push(num(recon) + ' call(s) were reconstructed from the decision rows, because the '
      + 'provider returned no call id before assay minted its own. They are priced at the rate '
      + 'shipping now; no rate was recorded at the time.');
  if (!c.output_calls)
    notes.push('No call has ever returned an output token count. Jev does not bill output, so '
      + 'nothing is missing from the dollars -- only from the counts.');
  if (wh.failed)
    notes.push(num(wh.failed) + ' warehouse statement(s) FAILED and are not in the money. A '
      + 'failed statement and an empty result are different facts here.');
  if (wh.unestimated)
    notes.push(num(wh.unestimated) + ' warehouse statement(s) could not be estimated and are not '
      + 'in the bytes. `dbt docs generate` gives assay the column types; `assay probe` gives it '
      + 'the row counts.');
  if (wh.calls && !wh.bytes_measured_calls)
    notes.push('Every byte figure here is an ESTIMATE from declared types and a known row count, '
      + 'never a number an adapter returned.');
  if (wh.calls && (!wh.rate_cards || !wh.rate_cards.length
                   || (wh.rate_cards.length === 1 && wh.rate_cards[0] === 'duckdb.local')))
    notes.push('The warehouse is priced as local DuckDB, which bills nothing. Set `cost.engine` '
      + 'and a rate in audit.yml if this warehouse charges.');
  if (notes.length)
    bits.push(block('What this does not cover', null,
      el('ul', {}, notes.map(t => el('li', {text: t})))));
  host.replaceChildren(...bits);
}

/* ------------------------------------------------------------------------------ Monitoring

   *** THE REPORT KNEW EVERYTHING ABOUT THE SQL AND NOTHING ABOUT WHETHER ANYBODY WAS WATCHING. ***
   `assay volume` measured the build cadence, every Elementary monitor's own freshness, what the
   declared tests are actually doing and which models have no volume history at all -- and wrote
   it to a JSON that only the review form read. So the page that answers "what is known about this
   warehouse" could not answer "is anything watching it", which is the same question one layer
   out.

   *** AND THIS TAB IS ABOUT THE MONITORING, NEVER ABOUT THE DATA. ***
   assay does not count rows over time and does not intend to; Elementary does. Everything here
   is an assertion that a monitor EXISTS, is CURRENT, and COVERS what matters. A relation that is
   `abandoned` means the monitor stopped, which is not the same as the data being late -- and in
   any monitoring view a monitor that stopped looks exactly like one that finds nothing. */
function monitoringTab(host) {
  const m = DATA.monitoring || {}, bits = [];
  const cad = m.cadence || {}, cov = m.test_coverage || {};
  const readings = m.readings || [], unwatched = m.unwatched || [], mf = m.monitoring || [];
  const stale = m.stale_failures || [];

  /* *** AN EMPTY TAB READS AS "NOTHING IS WRONG". *** It is not measured until somebody
     measures it, and this says exactly which command does that. */
  if (!cad.runs && !readings.length && !cov.declared) {
    bits.push(block('Nothing here has been measured',
      'This page carries the monitoring only when it is handed the measurement, because taking '
      + 'it needs your dbt connection and assay never holds a credential.', null));
    bits.push(el('pre', {text: 'assay volume --json > volume.json\n'
      + 'assay page assay.html --monitoring volume.json'}));
    bits.push(el('p', {class: 'note', text:
      'Nothing above is a statement about your monitoring. It says the numbers were not taken.'}));
    host.replaceChildren(...bits);
    return;
  }

  // ---- how often this project actually builds, which every threshold below is derived from
  const cadline = el('div', {});
  cadline.append(el('p', {text: cad.explain || 'the build cadence could not be read'}));
  if (cad.derived_staleness_days != null)
    cadline.append(el('p', {class: 'note', text:
      'So a monitor is called late after ' + cad.derived_staleness_days + ' day(s)'
      + (cad.floored ? ', which is the one-day floor rather than the measured gap: a threshold '
                     + 'cannot be shorter than a day' : '')
      + (cad.configured ? '. audit.yml sets this one deliberately.'
                        : '. Nothing is configured, so the derived number is what is in force.')}));
  bits.push(block('How often this project builds', 'Every threshold on this tab is derived from '
    + 'this rather than picked. A number somebody guesses cries wolf or stays quiet for a '
    + 'quarter.', cadline));

  // ---- the monitors, and whether each one is still being written to
  if (readings.length) {
    const live = readings.filter(r => r.state === 'live').length;
    const off = readings.length - live;
    const mbox = el('div', {});
    mbox.append(grid(readings, [
        {key: 'relation', label: 'relation', mono: 1, val: r => r.relation},
        {key: 'state', label: 'state', val: r => r.state,
         cell: r => el('span', {class: r.state === 'live' ? '' : 'bad',
                                text: String(r.state).replace(/_/g, ' ')})},
        {key: 'rows', label: 'rows', n: 1, val: r => r.rows},
        {key: 'newest', label: 'newest', val: r => r.newest || ''},
        {key: 'age', label: 'days since', n: 1, val: r => r.age_days,
         cell: r => el('span', {text: r.age_days == null ? '' : r.age_days.toFixed(1)})},
      ], {placeholder: 'filter monitors...', cap: 200}));
    /* *** THE SENTENCE IS THE EVIDENCE, AND A TABLE CELL CANNOT HOLD IT. ***
       Each reading carries the write history its threshold was derived from, which is what makes
       the state arguable rather than a verdict handed down. One line per monitor that is not
       live, inside the same block so it is part of that section rather than loose above the
       next heading. */
    for (const r of readings.filter(x => x.state !== 'live' && x.says))
      mbox.append(el('p', {class: 'note', text: r.says}));
    bits.push(block('The monitors themselves (' + num(readings.length) + ')',
      off === 0
        ? 'Every one of these has been written to recently.'
        : num(off) + (off === 1 ? ' of them has' : ' of them have')
          + ' stopped being written to, and a monitor that stopped reads exactly like one that '
          + 'finds nothing.',
      mbox));
  }

  // ---- what the declared tests are actually doing
  if (cov.declared) {
    const never = (cov.declared || 0) - (cov.ever_ran || 0);
    const parts = [
      {label: 'have produced a result', n: cov.ever_ran || 0, color: RAMP.declared},
      {label: 'declared, never run', n: never, color: RAMP.judged},
    ];
    const legend = el('div', {class: 'legend'}, parts.filter(x => x.n).map(x =>
      el('span', {class: 'lgi'}, [el('span', {class: 'sw', style: 'background:' + x.color}),
                                  el('span', {text: x.label + ' ' + num(x.n)})])));
    const box = el('div', {}, [stackedBar(parts.filter(x => x.n), cov.declared), legend]);
    box.append(el('p', {class: 'note', text:
      num(cov.declared) + ' test(s) declared, ' + num(cov.ever_ran || 0) + ' have ever produced a '
      + 'result, ' + num(cov.skipped_results || 0) + ' result(s) are SKIPPED. A test that never '
      + 'ran and a test that passed look identical in a summary, and only one of them has read '
      + 'your data.'}));
    bits.push(block('What your tests are doing', 'Declared is not run, and skipped is not '
      + 'passed.', box));
  }

  /* *** THE ONE NOBODY PREDICTED. ***
     A test whose LAST result was a failure and which has not run since. In any Elementary view
     it is indistinguishable from something failing right now, and it is neither: it is a
     question nobody has asked for two months. */
  if (stale.length) {
    bits.push(block(num(stale.length) + ' test(s) whose last result was a FAILURE, and which have '
      + 'not run since',
      'This is not a live failure and it is not a pass. It is an answer that has gone out of '
      + 'date, and it reads as a live failure in any view that sorts by status.',
      grid(stale, [
        {key: 'table', label: 'table', mono: 1, val: r => r.table, cell: r => link(r.table)},
        {key: 'kind', label: 'what failed', val: r => r.kind + ' · ' + (r.sub_type || '')},
        {key: 'age', label: 'days since', n: 1, val: r => r.age_days,
         cell: r => el('span', {text: r.age_days == null ? '' : r.age_days.toFixed(0)})},
      ], {placeholder: 'filter...', cap: 200})));
  }

  // ---- the monitoring findings, which are about the monitoring and not about the data
  if (mf.length) {
    bits.push(block(num(mf.length) + ' finding(s) about the monitoring',
      'Every one of these is a statement about whether something is watching. None of them is a '
      + 'statement about your data.',
      el('div', {}, mf.map(f => {
        const row = el('div', {class: 'mfrow'});
        row.append(el('span', {class: 'rlab mono', text: f.check.replace(/_/g, ' ')}));
        row.append(el('span', {text: f.summary}));
        return row;
      }))));
  }

  // ---- and the models nothing watches at all
  if (unwatched.length) {
    const worst = unwatched.filter(u => u.marts).length;
    bits.push(block(num(unwatched.length) + ' model(s) with nothing watching them',
      num(worst) + ' of them have a mart downstream, which is what makes an unnoticed change '
      + 'expensive. assay does not measure volume and does not intend to; `elementary-data` does, '
      + 'and it is a dbt package.',
      grid(unwatched, [
        {key: 'model', label: 'model', mono: 1, val: r => r.model, cell: r => link(r.model)},
        {key: 'marts', label: 'marts downstream', n: 1, val: r => r.marts},
        {key: 'descendants', label: 'descendants', n: 1, val: r => r.descendants},
      ], {placeholder: 'filter models...', cap: 400, sort: 'marts', dir: -1})));
  }

  host.replaceChildren(...bits);
}

/* ---------------------------------------------------------------------------------- tabs */
const VIEWS = {models: modelsTab, chain: chainTab, claims: claimsTab, findings: findingsTab,
               areas: areasTab,
               suggest: suggestTab, monitoring: monitoringTab,
               answers: answersTab, spend: spendTab, questions: questionsTab, config: configTab,
               understood: understoodTab};
const built = {};
function open(name) {
  document.querySelectorAll('nav button').forEach(b =>
    b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('.panel').forEach(p => { p.hidden = p.id !== 'p-' + name; });
  const host = document.getElementById('p-' + name);
  /* Built once, on first open. A 358-model warehouse renders eight tabs' worth of tables in well
     under a second, but there is no reason to render seven of them nobody has looked at. */
  if (!built[name]) { built[name] = 1; VIEWS[name](host); }
  /* *** replaceState THROWS ON file:// IN SOME BROWSERS, AND file:// IS THE POINT. ***
     A SecurityError here would abort `open` after the panels were swapped but before anything
     was built, so a tab would go blank. The hash is a convenience; the tab is not. */
  try { if (location.hash.slice(1) !== name) history.replaceState(null, '', '#' + name); }
  catch (e) { /* no deep link, and every tab still works */ }
}
/* *** A FIXED CARD MUST NOT OUTLIVE THE THING IT POINTS AT. ***
   It sits on the body, so nothing removes it when the panel under it changes. Scrolling moves
   the node out from under it; switching tabs replaces everything it described. Both dismiss it,
   as do Escape and a click anywhere else -- the last one being the only way out on a touch
   device, where there is no canvas to click. */
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') dismissCards(); });
document.addEventListener('click', ev => {
  if (!ev.target.closest || !ev.target.closest('.pop')) dismissCards();
}, true);
document.addEventListener('scroll', () => dismissCards(), true);
window.addEventListener('resize', () => dismissCards());

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => { dismissCards(); open(b.dataset.tab); }; });
open(VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : 'understood');
"""
