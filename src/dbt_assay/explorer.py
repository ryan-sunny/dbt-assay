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
body{margin:0;background:var(--paper);color:var(--ink);font-variant-numeric:lining-nums;
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
nav{display:flex;gap:4px 0;flex-wrap:wrap;margin-top:8px;align-items:flex-end}
.navgroup{display:inline-flex;flex-direction:column;flex:0 0 auto;padding-right:8px;
margin-right:8px;border-right:1px solid var(--rule)}
.navgroup:last-child{border-right:0;margin-right:0}
.navlab{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
padding:0 11px;font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif}
.navbtns{display:flex;flex-wrap:nowrap}
/* *** ONE ROW. *** "the tabs go over to a second row. unacceptable". Set in normal case, the strip
   is a third narrower than in spaced capitals; it steps down with the window, then drops the
   counts (still in each tab's tip), and on a phone it is one menu button. It never wraps. */
nav{flex-wrap:nowrap !important}
nav .navbtns button{padding:4px 9px 6px;font-size:16px;letter-spacing:0;text-transform:none}
nav .navbtns button b{font-size:12.5px;margin-left:5px}
.navmenu{display:none}
@media (max-width:1440px){
  nav .navbtns button{font-size:14.5px;padding:4px 7px 6px}
  .navlab{padding:0 7px;font-size:10.5px}
  .navgroup{padding-right:5px;margin-right:5px}
}
@media (max-width:1280px){ nav .navbtns button b{display:none} }
@media (max-width:1040px){
  nav .navbtns button{font-size:14px;padding:4px 5px 6px}
  .navlab{padding:0 5px}
  .navgroup{padding-right:3px;margin-right:3px}
}
@media (max-width:940px){
  .navmenu{display:inline-flex;align-items:center;gap:6px;margin:8px 0 10px;appearance:none;
  background:#f1ede4;border:1px solid var(--rule);padding:7px 12px;font:inherit;font-size:16px;
  color:var(--ink);cursor:pointer}
  nav{display:none;position:absolute;left:16px;right:16px;z-index:60;background:var(--paper);
  border:1px solid var(--ink);box-shadow:3px 3px 0 rgba(26,23,20,.14);padding:8px 0;
  flex-direction:column;overflow:auto;max-height:75vh}
  nav.open{display:flex}
  .navgroup{border-right:0;margin:0;padding:4px 0;display:flex;align-items:stretch;width:100%}
  .navlab{text-align:left;padding:4px 14px 2px}
  .navbtns{flex-direction:column;align-items:stretch;width:100%}
  nav .navbtns button{width:100%}
  nav .navbtns button{text-align:left;font-size:16px;padding:7px 14px;border-bottom:0;
  border-left:3px solid transparent}
  nav .navbtns button[aria-selected=true]{border-left-color:var(--ink)}
  nav .navbtns button b{display:inline}
  header{position:relative}
}
nav button{appearance:none;border:0;border-bottom:3px solid transparent;background:none;
font-family:Fell,Georgia,serif;font-size:15px;letter-spacing:.06em;text-transform:uppercase;
color:var(--ash);padding:7px 15px 6px;cursor:pointer;margin-bottom:-3px}
nav button:hover{color:var(--ink)}
nav button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--ink)}
nav button b{font-weight:400;color:var(--faint);margin-left:6px;font-size:12px;
letter-spacing:0;text-transform:none}

.spark{display:flex;align-items:flex-end;gap:2px;height:44px;max-width:600px;margin:12px 0 0}
.spark span{flex:1 1 0;min-width:2px;background:var(--ash)}
.spark span:last-child{background:var(--ink)}
/* the sections: the same running head, one size up, and Settings set apart at the right */
.secbtns{display:flex;flex-wrap:nowrap}
nav .secbtns button,nav .setbtn{font-size:17px;letter-spacing:0;text-transform:none;
padding:5px 14px 7px}
nav .setbtn{margin-left:auto;font-size:15px;color:var(--faint)}
/* the row under them: the views of the section you are in */
.subnav{display:flex;flex-wrap:wrap;gap:0;border-top:1px solid var(--rule);margin-top:3px}
/* *** THE COUNTS, ONCE, ON THE OVERVIEW. *** (Ryan: "i dont want those there at all theyre on
   every tab ... it just jumps you all over the place") A label and its number per row, even
   numerals, no colour; a row opens its list in place, under the counts. */
.nextgrid{display:grid;grid-template-columns:minmax(220px,280px) minmax(0,1fr);gap:0 28px;
align-items:stretch;margin:0 0 22px}
.countpanel{position:relative;min-height:200px;border-left:1px solid var(--rule)}
.countinner{position:absolute;inset:0;overflow-y:auto;padding:0 0 0 16px}
.clist.fixed{table-layout:fixed}
.clist.fixed td.one:nth-child(1){width:30%}
.clist.fixed td.one:nth-child(2){width:28%}
.clist td.one{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.clist td.one.wide{white-space:normal}
.changesbelow{margin-top:4px}
.clist td.clears{white-space:nowrap;width:1%;padding-left:14px}
.clist td.clears .dec{font-size:12.5px;color:var(--ash)}
.clist td.ctitle2{overflow-wrap:anywhere}
#p-understood{scrollbar-gutter:stable;padding-right:6px}
.counts{border-collapse:collapse;width:100%;font-size:15px}
.counts td{padding:5px 4px;border-bottom:1px solid var(--rule2);cursor:pointer}
.counts td.cn{text-align:right;font-variant-numeric:lining-nums tabular-nums;font-feature-settings:"lnum","tnum"}
.counts tr:hover td{background:#f2efe7}
.counts tr.on td{background:#efe9dc;box-shadow:none}
.counts tr.on td:first-child{box-shadow:inset 3px 0 0 var(--ink)}
.reach .rg{margin:2px 0 0}
.reach .rg summary{cursor:pointer;font-variant-numeric:lining-nums tabular-nums}
.reach .rgi{color:var(--ash);font-size:13.5px;margin:2px 0 6px 14px}
.clist{border-collapse:collapse;width:100%;font-size:14px}
.clist td{padding:4px 8px 4px 0;border-bottom:1px solid var(--rule2);vertical-align:top}
.clist td.cn{text-align:right;white-space:nowrap;font-variant-numeric:lining-nums tabular-nums}
.clist td.sum{color:var(--ash)}
.clist.changes td{padding:6px 10px 6px 0}
.clist td.rank{color:var(--faint);width:1.5em}
@media (max-width:820px){.nextgrid{display:block}.counts{margin-bottom:14px}
.countpanel{height:320px;border-left:0}.countinner{padding:0}}
.fblock{border-top:1px solid var(--rule);padding-top:10px;margin-top:14px}
.fbucket{font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);
margin-bottom:4px}
.subnav[hidden]{display:none}
.subnav button{appearance:none;border:0;border-bottom:2px solid transparent;background:none;
font-family:Fell,Georgia,serif;font-size:14.5px;color:var(--ash);padding:5px 11px 5px;
cursor:pointer;margin-bottom:-3px}
.subnav button:hover{color:var(--ink)}
.subnav button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--ink)}
.subnav button b{font-weight:400;color:var(--faint);margin-left:5px;font-size:12px}
.formpanel{overflow:hidden;margin:-20px -26px -22px;height:calc(100% + 42px)}
.formframe{display:block;width:100%;height:100%;border:0;background:var(--paper)}
.formnone{padding:26px}
@media (max-width:940px){
  /* the page scrolls as a whole here, so the embedded form needs a height of its own */
  #p-form{height:auto;margin:0}
  #p-form .formframe{height:88vh}
  .secbtns{flex-direction:column;width:100%}
  nav .secbtns button,nav .setbtn{width:100%;text-align:left;border-bottom:0;
  border-left:3px solid transparent;padding:7px 14px;margin:0}
  nav .secbtns button[aria-selected=true],nav .setbtn[aria-selected=true]{
  border-left-color:var(--ink)}
}
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
.mtop{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:4px 36px;
margin:0 0 12px;align-items:start}
.mtop > .mchart{margin:0}
.mtop > div:last-child:not(.mfacts){grid-column:1 / -1}
.mfacts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 18px}
.mfv{font-size:22px;font-family:Fell,Georgia,serif;line-height:1.1}
.mfv.bad{color:var(--rust)}
.mfl{font-size:13px;color:var(--ash)}
@media (max-width:820px){.mtop{grid-template-columns:1fr}.mfacts{grid-template-columns:1fr 1fr}}
.mchart{margin:2px 0 12px}
.mrow{display:grid;grid-template-columns:minmax(0,210px) minmax(60px,1fr) auto;gap:10px;
align-items:center;padding:3px 0}
.mlab{font-size:12px;overflow-wrap:anywhere}
.mtrack{position:relative;height:12px;border-bottom:1px solid var(--rule2);display:block}
.mfill{position:absolute;left:0;top:0;height:12px;background:var(--iron)}
.mfill.late{background:var(--rust)}
.mmark{position:absolute;top:-3px;width:2px;height:18px;background:var(--ink)}
.mval{font-size:13px;color:var(--ash);white-space:nowrap;font-variant-numeric:tabular-nums}
.apart{font-size:13px;color:var(--ash);margin-top:2px}
.panel.cfgpanel{display:flex;flex-direction:column;overflow:hidden}
.panel.cfgpanel[hidden]{display:none}
.cfgpanel > .cfgtop{flex:0 0 auto}
.cfgpanel > .cfgtop .tabcut{height:170px;margin-bottom:6px}
.cfgpanel > .drillhost{flex:1 1 auto;min-height:320px;border-top:1px solid var(--rule);padding-top:12px}
@media (max-width:820px){.panel.cfgpanel{display:block;overflow:visible}}
.pkind{font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);
margin:0 0 2px}
.plead{font-size:15px;color:var(--ink);margin:6px 0 4px;line-height:1.5}
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
table.counts tr.on td:not(:first-child){box-shadow:none}
.n{text-align:right;font-variant-numeric:tabular-nums}
/* *** WRAPPED, NEVER CUT. *** A cell used to end in an ellipsis at 300px, which hid the part of a
   claim that said what it was about. It wraps now, and the row is as tall as what it holds. */
td.clip{overflow-wrap:break-word;min-width:10em}
td{overflow-wrap:break-word}
/* Code has runs with no break in them (`home|deck|fence|pool|...`), and `break-word` does not
   lower a table column's minimum width, so a code cell may break anywhere as a last resort. */
td.mono.clip{overflow-wrap:anywhere}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}

/* ---- the two-pane shell. Panes are separated by a rule, not by two boxes. */
.wrap2{display:grid;grid-template-columns:minmax(300px,1fr) minmax(0,1.55fr);gap:0;
align-items:stretch;height:100%}
.wrap2.wide{grid-template-columns:minmax(400px,1.1fr) minmax(0,1.3fr)}
.wrap2.narrow{grid-template-columns:minmax(300px,420px) minmax(0,1fr)}
.wrap2.narrow > .pane{padding-right:14px;border-right:1px solid var(--rule)}
.wrap2.narrow > .detail{padding-left:22px}
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
.split{display:inline-flex;gap:14px;align-items:baseline}
.split .gsort{font-size:14px}
.pager[hidden]{display:none}
.pg{appearance:none;border:1px solid var(--rule);background:none;font:inherit;font-size:13px;
color:var(--ink);padding:1px 9px;cursor:pointer}
.pg:hover:not(:disabled){border-color:var(--ink)}
.pg:disabled{color:var(--faint);cursor:default}
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
.wterms{display:grid;grid-template-columns:auto 1fr;gap:1px 12px;margin:4px 0 2px;font-size:13px}
.wterms .n{font-variant-numeric:tabular-nums}
.wbox .note{margin:4px 0 0;font-size:12.5px}

/* ---- a pill is a lettered tag, the way a part is lettered on a plate. */
.pill{display:inline-block;font-family:Fell,Georgia,serif;font-size:11.5px;padding:0 6px;
border:1px solid var(--rule);color:var(--ash);white-space:nowrap;letter-spacing:.03em}
/* In a table cell a pill wraps like the text beside it, or it pushes its column off the pane. */
td .pill{white-space:normal}
.pill.declared,.pill.on{color:var(--iron);border-color:var(--iron)}
.pill.derived,.pill.observed{color:var(--ash)}
.pill.judged{color:var(--ember);border-color:var(--ember)}
.pill.bad{color:var(--rust);border-color:var(--rust)}
/* A premise's status: rust when it broke, ink when it holds, ember when only a judgment carries
   it, faint when nothing has checked it. Never a colour without its word. */
.pill.broken{color:var(--rust);border-color:var(--rust)}
.pill.holding,.pill.proven{color:var(--ink);border-color:var(--ink)}
.pill.assumed{color:var(--ember);border-color:var(--ember)}
.pill.unchecked,.pill.unknown{color:var(--faint);border-color:var(--rule)}
.plink{margin-left:10px;font-size:13px;white-space:nowrap}
.ulist .urow{padding:4px 0;border-bottom:1px solid var(--rule)}
.ulist .urow:last-child{border-bottom:0}
.usub{font-size:13px;color:var(--ash);margin-top:2px}
.nowrap{white-space:nowrap}
.fact.bad{color:var(--rust)}
/* A note is a FACT in a pane now, never a caption: every explanation moved to a tip, so what is
   left is something true of this warehouse, and it is set in ink where it will be read. */
.note{color:var(--ink);font-size:13.5px;margin:8px 0 0;max-width:none}
.fact{color:var(--ink);font-size:14px;margin:0 0 10px}
/* ---- the tip. One element for the page, shown at once, positioned inside the window. */
.hint{text-decoration:underline dotted var(--faint);text-underline-offset:3px;cursor:help}
th.hint{text-decoration-color:var(--faint)}
.tipbox{position:fixed;z-index:70;max-width:360px;background:var(--ink);color:var(--paper);
font:13px/1.45 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;padding:7px 10px;
pointer-events:none;white-space:pre-line;box-shadow:2px 2px 0 rgba(26,23,20,.14);
text-transform:none;letter-spacing:normal}
.tipbox[hidden]{display:none}
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
.days.dense{gap:1px}
.days.dense .day{min-width:5px;flex:1 1 5px}
.days.dense .daylab{overflow:visible}
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
.md > p + p{margin-top:8px}
.md > :first-child{margin-top:0}
.md > :last-child{margin-bottom:0}
.mdh{margin:14px 0 5px;font-family:Fell,Georgia,serif;font-size:15px;font-weight:400}
.mdlist{margin:6px 0;padding-left:20px;font-size:13.5px;color:var(--ink)}
.mdlist li{margin:2px 0}
.mdpre{white-space:pre-wrap;font-size:12px;background:#f4f1e9;border:0;
border-left:2px solid var(--rule);padding:8px 12px;margin:8px 0;overflow-x:auto}
.md code,code.tick{font-size:12px;background:#f4f1e9;padding:0 3px;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-style:normal}

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

/* ==== the feedback round (assay-feedback.md G2, G3, G5, P6, P10) ====================== */
/* G3. The display face is for display. Anything small is set in the text face, upright, at a size
   that reads without zooming. */
.sub,nav button b,.count,th,.gsect,.kv dt,.tlab,.rsub,.srclab,.daylab,summary,label.chk,
.linhint,.plate figcaption,.detail h3,.ovblock h3,.sval,.gsort,.pg,select{
font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-style:normal}
.sub{font-size:13px}
nav button b{font-size:12.5px;color:var(--ash)}
.count{font-size:13px;color:var(--ash)}
th{font-size:12px;letter-spacing:.05em}
.kv dt{font-size:13px;color:var(--ash)}
.tlab,.detail h3,.ovblock h3,.gsect{font-size:12.5px;letter-spacing:.06em}
.daylab{font-size:11px}
.srclab{font-size:13.5px}
label.chk{font-size:13.5px}
/* G2, P6. One badge: outlined, the text face, one line, and never flush against what precedes it. */
.pill{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-size:12px;
line-height:1.5;padding:0 7px;white-space:nowrap;vertical-align:1px;margin-left:7px}
.pill:first-child{margin-left:0}
td > .pill:first-child,td > .badge:first-child > .pill,dd > .pill:first-child{margin-left:0}
.badge{white-space:nowrap;margin-left:7px}
.badge:first-child{margin-left:0}
.badge .pill{margin-left:0}
.pconf{font-size:12px;color:var(--ash);margin-left:5px;font-variant-numeric:tabular-nums}
td .pill{white-space:nowrap}
/* G5. An input looks like an input: a light field, a border, room inside it, and a placeholder
   that is plainly a placeholder. */
input[type=search],input[type=text],select{background:#f1ede4;border:1px solid var(--rule);
padding:6px 10px;font-size:14px}
input[type=search]:focus,input[type=text]:focus,select:focus{border-color:var(--ink);
outline:none;background:#fbf9f4}
::placeholder{color:var(--faint);font-style:italic;opacity:1}
/* P10. Narrow screens: one column, the document scrolls, and a table too wide for its box
   scrolls inside that box rather than off the edge of the screen. */
@media (max-width:820px){
  body{height:auto;overflow:auto}
  main{padding:14px 16px}
  .panel{height:auto;overflow:visible}
  .wrap2,.wrap2.wide,.wrap3{display:block;height:auto}
  .pane,.gnav{height:auto;border-right:0;padding-right:0;padding-left:0;margin-bottom:14px}
  .wrap3 > .pane{padding-left:0}
  .glist{max-height:260px}
  .list{max-height:70vh;overflow:auto}
  .gridhost{height:auto}
  .detail{height:auto;padding:12px 0 20px;border-top:1px solid var(--rule)}
  .detail h2,.detail .path,.kv dd,.prose,.quote,p,h2,h3,.mdlist{overflow-wrap:anywhere}
  .ticket{grid-template-columns:1fr}
  .tcol + .tcol{padding-left:0;border-left:0;border-top:1px solid var(--rule)}
  .ticketwrap{grid-template-columns:1fr}.ticketcut{display:none}
  .rank{grid-template-columns:minmax(0,40%) minmax(40px,1fr) auto}
  .rlab{white-space:normal;overflow-wrap:anywhere}
  .rval{white-space:normal}
  .tiles{grid-template-columns:1fr 1fr}
  .tile + .tile{padding-left:0;border-left:0}
  .days{overflow-x:auto}
  .tabcut{display:none}
  input[type=search]{min-width:0;width:100%}
  .bar{flex-wrap:wrap}
  .linwrap{height:320px}
  nav button{padding:6px 9px 5px;font-size:13px}
  .day{min-width:0;flex:1 1 0}
  .daylab{display:none}
  .day:nth-last-child(7n+1) .daylab{display:block;white-space:nowrap}
}
/* A phone has no room for columns, so a row becomes lines: each value under its own label. */
@media (max-width:560px){
  table,thead,tbody,tr,td{display:block;width:100%}
  thead{display:none}
  tr{border-bottom:1px solid var(--rule2);padding:6px 0}
  td{border:0;padding:2px 4px;text-align:left}
  td.n{text-align:left}
  td[data-label]:not([data-label=""])::before{content:attr(data-label);display:block;
  font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--faint)}
  tr.on td{box-shadow:none}
  tr.on{box-shadow:inset 3px 0 0 var(--ink);background:#efe9dc}
  /* a label and its number stay on one line */
  table.counts{display:table}
  table.counts tbody{display:table-row-group}
  table.counts tr{display:table-row;padding:0;box-shadow:none}
  table.counts td{display:table-cell;width:auto;padding:5px 4px;border-bottom:1px solid var(--rule2)}
  table.counts td.cn{text-align:right}
}
footer{color:var(--faint);font-size:12px;padding:14px 26px;border-top:3px double var(--ink);
font-family:Fell,Georgia,serif;font-style:italic}
footer b{font-style:normal;font-weight:400;color:var(--ash)}
"""

JS = r"""
const $ = (s, r) => (r || document).querySelector(s);
/* *** A `title` IS A TIP THAT ARRIVES A SECOND LATE, UNSTYLED, AND NOT AT ALL ON TOUCH. ***
   Every `title` on this page is written as `data-tip`, which the one tip below shows at once.
   `tip:` does the same and also marks the element with a dotted underline, for the places where a
   reader should know there is more to read: a column header, a section heading, a label. */
const el = (t, a, kids) => { const n = document.createElement(t);
  for (const k in (a || {})) { if (k === 'text') n.textContent = a[k];
    else if (k === 'html') n.innerHTML = a[k];
    else if (k === 'title') { if (a[k] != null && a[k] !== '') n.setAttribute('data-tip', a[k]); }
    else if (k === 'tip') { if (a[k]) { n.setAttribute('data-tip', a[k]); n.classList.add('hint'); } }
    else if (a[k] != null) n.setAttribute(k, a[k]); }
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
  /* *** AND A BREAK POINT MUST NOT CUT A CODE SPAN IN TWO. *** (P9) Splitting at `_` and `.`
     put the two backticks of `on a.known_section_id is null` in different text nodes, so the
     backtick renderer never saw a pair and the ticks stayed on the page. Code spans are made
     here, before the text is split. */
  const f = document.createDocumentFragment();
  const brk = (into, t) => t.split(/(?<=[_./,|])/).forEach((p, i) => {
    if (i) into.append(document.createElement('wbr'));
    into.append(document.createTextNode(p));
  });
  String(s == null ? '' : s).split(/(`[^`\n]+`)/).forEach(part => {
    if (/^`[^`\n]+`$/.test(part)) {
      const c = document.createElement('code'); c.className = 'tick';
      brk(c, part.slice(1, -1)); f.append(c);
    } else if (part) brk(f, part);
  });
  return f;
}
const pct = x => (x == null ? '' : Math.round(x * 100) + '%');

/* A Fact with its provenance. A value with no source is a rumour, so the pill is never dropped. */
/* *** ONE BADGE, EVERYWHERE. ***
   Badges wrapped onto two lines inside narrow columns, mixed filled and outlined, serif and mono,
   and "@0.33" read as part of the label. A badge is now one word or phrase that never wraps, and
   how sure the reading was sits beside it as a plain number, outside the border. */
function badge(label, cls, conf) {
  const b = el('span', {class: 'pill ' + (cls || ''), text: label});
  if (conf == null) return b;
  return el('span', {class: 'badge'}, [b, el('span', {class: 'pconf', text: Number(conf).toFixed(2),
    tip: 'How sure the reading was, from 0 to 1.'})]);
}

/* A premise's status as one badge: the few words (`never ran`, `passing`, `counted duplicates`)
   in the status's colour, and the evidence behind it as the tip. */
function premBadge(status, label, why) {
  const b = badge(label || status, status || 'unknown');
  if (why) b.setAttribute('data-tip', why.replace(/`/g, ''));
  return b;
}

function fact(f) {
  if (!f) return el('span', {class: 'tot', text: 'not settled'});
  const v = Array.isArray(f.value) ? f.value.join(', ') : String(f.value);
  const s = el('span', {});
  s.append(el('span', {text: v}));
  s.append(badge(f.source, f.source || '', f.confidence));
  if (f.premise) s.append(premBadge(f.premise.status, f.premise.label, f.premise.why));
  if (f.resting_on) s.append(badge('rests on a premise', 'bad'));
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
    const th = el('th', {text: c.label, class: c.n ? 'n' : '', tip: c.tip});
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
      count.textContent = opts.countLine ? opts.countLine(view, from, to)
        : num(from) + '\u2013' + num(to) + ' of ' + num(view.length)
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
                                       + (c.clip ? 'clip' : ''), 'data-label': c.label || null},
          [c.cell ? c.cell(r) : typeof c.val(r) === 'number'
            ? el('span', {text: cellText(c.val(r))})
            : el('span', {}, [wbr(cellText(c.val(r)))])])));
      if (opts.pick) tr.onclick = () => { body.querySelectorAll('tr.on').forEach(x => x.classList.remove('on'));
        tr.classList.add('on'); opts.pick(r); };
      tr._row = r;                          // what a link finds a row by, never its text
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
function section(title, node, tipText) {
  const h = el('h3', {}, [el('span', {text: title, tip: tipText})]);
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


def _models_line(meta: dict) -> str:
    """The project's own models, with installed packages' said apart: 403 read as the project's
    size when 75 of them were dbt_project_evaluator's and elementary's."""
    yours, packaged = meta.get("yours"), meta.get("packaged") or 0
    if yours is None:
        return f"{meta['models']:,} models"
    return f"{yours:,} models" + (f" (+{packaged:,} in installed packages)" if packaged else "")


def _badge(n) -> str:
    """A tab's number: a count, grouped, or a phrase like "433 of 697"."""
    if n is None:
        return ""
    return f"<b>{html.escape(n) if isinstance(n, str) else format(n, ',')}</b>"


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
        # Ryan: the tab's number is what is proven, "433 of 697"; without certificates, the
        # premises measured false (B4: never "not holding", which counted the unchecked as wrong).
        # Installed packages' premises are not this project's to fix (L3).
        "guarantees": ((f"{sum(1 for r in data['proofs'] if r.get('status') == 'proven'):,} of "
                        f"{len(data['proofs']):,}") if data.get("proofs") else
                       sum(1 for p in data.get("premises") or [] if p.get("status") == "broken"
                           and not p.get("package")) if data.get("premises") else None),
    }
    # *** THE OVERVIEW IS THE WAY IN, NOT THE LAST TAB. ***
    # It is the only surface here with an argument to make rather than a table to show, and a
    # person opening this file has not yet picked a model to look at. Landing on 358 rows asks
    # them to choose before they have been told anything.
    # *** TWELVE TABS IN A ROW, AND NO HINT WHERE TO START. *** (P7)
    # The strip is in reading order and grouped under small labels: start here, what is wrong,
    # the project itself, what assay asked, and the setup. `NAV_GROUPS` below names the runs.
    tabs = [
        ("understood", "Overview", None),
        ("findings", "Findings", counts["findings"]),
        ("areas", "Areas", counts["areas"]),
        # *** IS ANYTHING WATCHING THIS, AND ARE THE TESTS ACTUALLY RUNNING. ***
        # Present whether or not the numbers were taken: a tab that appears only when somebody
        # passed `--monitoring` is a tab nobody learns exists, and its absence reads as a tool
        # that does not do this rather than as a measurement not yet made.
        # *** WHAT THE FINDINGS REST ON, AND WHAT IS PROVEN. *** Before Monitoring (Ryan). The
        # number is the properties proven of all stated, or the broken premises without proofs.
        ("guarantees", "Guarantees", counts["guarantees"]),
        ("monitoring", "Monitoring", counts["monitoring"]),
        ("models", "Models", counts["models"]),
        ("chain", "The chain", counts["edges"]),
        ("claims", "Claims", counts["claims"]),
        ("answers", "Answers", counts["decisions"]),
        ("questions", "Questions", counts["questions"]),
        ("suggest", "What to configure", counts["suggestions"]),
        ("config", "Config", None),
        ("spend", "Spend", None),
    ]
    # Fix and Decide ARE the review form, embedded; a second way into it in the header was one
    # more thing to read (Ryan).
    form_link = ""
    # *** WHAT A TAB IS, ON THE TAB. ***
    # Each tab opened on a grey sentence saying what it was, and nobody read them ("its random
    # text at the top ... your eyes dont even notice it"). The sentence is the tab's tip now, where
    # somebody deciding whether to click it is already looking.
    tips = {
        "understood": "What assay read, judged and found, and how much of it a person has read.",
        "models": "Every model this project builds, and what one row of each one is.",
        "chain": "Every hop between models, drawn one neighbourhood at a time.",
        "claims": "Every sentence this project writes about itself, from descriptions and SQL "
                  "comments, and whether the code contradicts it.",
        "findings": "What assay found wrong, ranked by weight.",
        "areas": "Filters written the same way in several models, the one that differs from its "
                 "family, and one claim made about several models.",
        "monitoring": "Whether anything is watching this warehouse, and whether the tests run.",
        "guarantees": "What the findings rest on: every key a grain or a held-back finding "
                      "assumes is unique, what measured it, and since when.",
        "suggest": "What audit.yml should say, from what assay measured. It never writes a meaning.",
        "answers": "The latest answer to every question asked about this project.",
        "spend": "What the model calls and the warehouse statements cost.",
        "questions": "Every question assay asks, in full, and how often a person has ruled on it.",
        "config": "The settings assay is using, and everything you wrote by hand.",
    }
    # and what the number beside it counts, because "Monitoring 5" beside "Models 358" invites
    # reading both as sizes, and one of them is a count of findings.
    counted = {"models": "models", "chain": "hops between models", "claims": "sentences",
               "findings": "findings", "areas": "rows across its three lists",
               "monitoring": "findings about the monitoring", "suggest": "candidates",
               "guarantees": ("properties proven, of all assay stated a proof for"
                              if data.get("proofs") else
                              "premises measured false (broken): a count or a failed test says "
                              "otherwise"),
               "answers": "live answers", "questions": "questions"}
    for t, _label, n in tabs:
        if n is not None and t in counted:
            tips[t] = (f"{tips.get(t, '')}\nThe number is "
                       f"{n if isinstance(n, str) else format(n, ',')} {counted[t]}.")
    # which store and run the Guarantees numbers were counted from (L4), where the strip was
    cf = (data.get("meta") or {}).get("counted") or {}
    if cf.get("run"):
        tips["guarantees"] = (f"{tips.get('guarantees', '')}\nCounted from "
                              f"{cf.get('store') or 'the store'}, run {cf['run']} at {cf.get('at')}.")
    # *** FOUR SECTIONS BY THE JOB, NOT THIRTEEN TABS BY THE KIND OF DATA. *** (Ryan) Overview:
    # is it healthy and getting better. Fix: what to change next. Decide: only the calls a person
    # has to make. Explore: one thing at a time, with every list the tabs used to be. Settings
    # hold what is configured. Fix and Decide are the review form, embedded.
    labels = {t: label for t, label, _n in tabs}
    fixes_n = len(data.get("fixes") or [])
    cards_n = ((data.get("meta") or {}).get("review") or {}).get("groups")
    sections = [("overview", "Overview", None,
                 "Whether this warehouse is healthy and getting better, and what to change next."),
                ("fix", "Fix", fixes_n or None,
                 ("The changes that clear the findings, the most per decision first. One "
                  "decision per change.")),
                ("decide", "Decide", cards_n or None,
                 ("Only the calls a person has to make: the queued judgment calls, in groups, "
                  "the failing rows, the words.")),
                ("explore", "Explore", None,
                 "Every model, hop, claim and answer, and every finding by kind.")]
    nav = ('<span class="secbtns">' + "".join(
        f'<button role="tab" data-section="{k}" aria-selected="{"true" if k == "overview" else "false"}" '
        f'data-tip="{e(tip)}">{e(label)}{_badge(n)}</button>' for k, label, n, tip in sections)
        + '</span><button class="setbtn" data-section="settings" aria-selected="false" '
          'data-tip="What assay is configured with, what to configure next, every question it '
          'asks, and what it has cost.">Settings</button>')
    subviews = {
        "explore": ["findings", "areas", "guarantees", "monitoring", "models", "chain", "claims",
                    "answers"],
        "settings": ["suggest", "config", "questions", "spend"],
    }
    grouped = {"understood"} | {v for vs in subviews.values() for v in vs}
    assert grouped == {t for t, _l, _n in tabs}, "a view is in no section"
    sub_json = json.dumps({"subviews": subviews, "labels": labels,
                           "counts": {t: n for t, _l, n in tabs},
                           "tips": tips}, default=str)
    sub_blob = sub_json.replace("</", "<\\/").replace("<!--", "<\\!--")
    panels = "".join(f'<div class="panel" id="p-{t}"{"" if i == 0 else " hidden"}></div>'
                     for i, (t, _l, _n) in enumerate(tabs)) + \
        '<div class="panel formpanel" id="p-form" hidden></div>'


    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(meta['project'])} &middot; assay</title>
<link rel="icon" href="{FAVICON}">
<style>{FONT_CSS}{CSS}.tgrid{{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;margin:0 0 10px;font-size:13px;line-height:1.35}}.tgrid .tl{{color:var(--ash)}}.tgrid .tn{{color:var(--ash)}}
</style></head><body>
<header>
<h1>{MARK_SVG}<span class="hname">{e(meta['project'])}</span><span>everything assay knows</span></h1>
<div class="sub">{_models_line(meta)} &middot; {meta['sources']} sources &middot;
manifest generated {e(str(meta['generated_at']))} &middot;
<span class="hint" data-tip="The build is a hash of the code that rendered this page: two pages claiming one version and differing here came from two different installs.&#10;&#10;The page is deterministic. It carries the manifest's own generated_at and never a wall clock, and every list arrives sorted, so a rerun against an unchanged store writes an identical file.">assay {e(meta['version'])} &middot; build {e(build_fingerprint())}</span>{form_link}</div>
<button class="navmenu" id="navmenu" aria-expanded="false"><span id="navcur">Overview</span> ▾</button>
<nav role="tablist">{nav}</nav>
<div class="subnav" id="subnav"></div>
</header>
<main>{panels}</main>

<!-- THE ONE SWAPPABLE LINE. Embedded here; a server would make this a fetch and nothing
     below would change. -->
<script id="assay-data" type="application/json">{blob}</script>
<script id="assay-sections" type="application/json">{sub_blob}</script>
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
      ? (opts.allRows ? opts.allRows().map(r => [r, ALL])
                      : opts.groups.flatMap(x => opts.rowsOf(x).map(r => [r, x])))
      : opts.rowsOf(g).map(r => [r, g]);
    for (const t of toggles) if (t.on) out = out.filter(p => t.where(p[0]));
    return out;
  }
  function rowsFor(g) {
    const out = rowsSplit(g);
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
    const b = el('button', {class: 'gitem' + (g === pickedGroup ? ' on' : '') + (n ? '' : ' zero')},
                 [el('span', {class: 'gname'}, [wbr(label)]), el('span', {class: 'gn', text: num(n)})]);
    if (sub) b.append(el('span', {class: 'gsub', text: sub}));
    b.onclick = () => { pickedGroup = g; facet = null; pickSplit(g); paintGroups(); draw(); };
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

  /* *** A FILTER IS A CONTROL, NOT A CHART. *** (P3)
     The answer values were a bar chart you clicked to filter, and clicking a bar to narrow a list
     "feels broken". It is a dropdown in the filter bar now, with each value's count in it. */
  function facetSelect(g) {
    if (!opts.facet) return null;
    const rows = rowsSplit(g);
    const counts = {};
    for (const p of rows) { const v = String(opts.facet.of(p[0])); counts[v] = (counts[v] || 0) + 1; }
    const vals = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    if (facet != null && !counts[facet]) facet = null;
    const sel = el('select', {title: 'Show only the rows with this ' + opts.facet.label + '.'});
    const opt = (v, t) => { const o = el('option', {value: v, text: t}); if (v === (facet ?? '')) o.selected = true; return o; };
    sel.append(opt('', 'every ' + opts.facet.label + ' (' + num(rows.length) + ')'));
    for (const [v, n] of vals) sel.append(opt(v, v + ' (' + num(n) + ')'));
    sel.onchange = () => { facet = sel.value === '' ? null : sel.value; draw(); };
    return sel;
  }

  /* *** THE ROWS THAT MATTER FIRST, ONE CLICK FROM THE REST. *** (P4)
     A split is a plain two-way switch above the list -- the answers that became findings, and
     the ones that did not -- each with its count, opening on the first that has any. */
  let splitVal = null;
  function rowsSplit(g) {
    const out = rowsBefore(g);
    return opts.split && splitVal != null ? out.filter(p => opts.split.of(p[0]) === splitVal) : out;
  }
  function pickSplit(g) {
    if (!opts.split) return;
    const base = rowsBefore(g);
    const first = opts.split.choices.find(([k]) => base.some(p => opts.split.of(p[0]) === k));
    splitVal = first ? first[0] : opts.split.choices[0][0];
  }
  function splitControl(g) {
    if (!opts.split) return null;
    const base = rowsBefore(g);
    return el('span', {class: 'split'}, opts.split.choices.map(([k, label, tipText]) => {
      const n = base.filter(p => opts.split.of(p[0]) === k).length;
      const b = el('button', {class: 'gsort' + (splitVal === k ? ' on' : ''),
                              text: label + ' (' + num(n) + ')', tip: tipText});
      b.onclick = () => { splitVal = k; facet = null; draw(); };
      return b;
    }));
  }

  /* *** THE COUNT IS FOR WHAT IS PICKED. *** (P1) The label said 122 with a model picked that
     had none, and the list under it was empty. It says the picked group's count first and the
     project's after it, and a group with none says so in the list. */
  const toggleBoxes = toggles.map(t => {
    const cb = el('input', {type: 'checkbox'});
    t.cb = cb;
    cb.onchange = () => { t.on = cb.checked; paintGroups(); draw(); };
    t.text = el('span');
    return el('label', {class: 'chk', title: t.title || t.label}, [cb, t.text]);
  });
  /* *** TWO PANES. *** (Ryan: "not enough screen space") With `groupSelect`, the groups are a
     dropdown in the rows' filter bar instead of a column of their own. */
  function groupSelect() {
    if (!opts.groupSelect) return null;
    const sel = el('select', {title: 'Show only the rows of one ' + (opts.groupNoun || 'group')});
    const o = (g, t) => { const x = el('option', {text: t}); x._g = g;
                          if (g === pickedGroup) x.selected = true; return x; };
    const on = toggles.filter(t => t.on).map(t => t.key);
    const cnt = g => opts.countOf ? opts.countOf(rowsBefore(g).map(p => p[0]),
                                                 g === ALL ? null : g, on)
                                  : rowsBefore(g).length;
    const unit = opts.countUnit ? ' ' + opts.countUnit : '';
    sel.append(o(ALL, 'every ' + (opts.groupNoun || 'group') + ' (' + num(cnt(ALL)) + unit + ')'));
    for (const g of opts.groups) {
      const n = cnt(g);
      if (n) sel.append(o(g, opts.chip(g) + ' (' + num(n) + unit + ')'));
    }
    sel.onchange = () => { pickedGroup = sel.options[sel.selectedIndex]._g; facet = null;
                           pickSplit(pickedGroup); draw(); };
    return sel;
  }
  function paintToggles() {
    for (const t of toggles) {
      if (t.cb) t.cb.checked = t.on;
      const base = pickedGroup === ALL
        ? (opts.allRows ? opts.allRows() : opts.groups.flatMap(x => opts.rowsOf(x)))
        : opts.rowsOf(pickedGroup);
      const g = pickedGroup === ALL ? null : pickedGroup;
      const here = opts.countOf ? opts.countOf(base.filter(t.where), g, [t.key])
                                : base.filter(t.where).length;
      const all = opts.countOf && g ? opts.countOf((opts.allRows ? opts.allRows()
        : opts.groups.flatMap(x => opts.rowsOf(x))).filter(t.where), null, [t.key]) : t.count;
      const u = opts.countUnit ? ' ' + opts.countUnit : opts.toggleUnit ? ' ' + opts.toggleUnit : '';
      t.text.textContent = t.label + (pickedGroup === ALL ? ' (' + num(here) + u + ')'
        : ' (' + num(here) + u + ' here \u00b7 ' + num(all) + ' in all)');
    }
  }

  let list = null;
  function draw() {
    const pairs = rowsFor(pickedGroup);
    const cs = (opts.colsFor ? opts.colsFor(pickedGroup) : null) || opts.rowCols;
    list = grid(pairs, cs.map(c => ({...c,
      val: p => c.val(p[0]), cell: c.cell ? p => c.cell(p[0]) : null})), {
      placeholder: opts.rowFilter || 'filter...', page: opts.pageSize || 200,
      countLine: opts.countLine ? (view, from, to) => opts.countLine(view.map(p => p[0]), from, to,
        pickedGroup === ALL ? null : pickedGroup, toggles.filter(t => t.on).map(t => t.key)) : null,
      sort: (opts.sortFor ? opts.sortFor(pickedGroup) : null) || opts.rowSort,
      dir: (opts.dirFor ? opts.dirFor(pickedGroup) : null) || opts.rowDir || 1, scroll: 1,
      controls: [groupSelect(), splitControl(pickedGroup), facetSelect(pickedGroup),
                 ...toggleBoxes].filter(Boolean),
      pick: p => showOne(p[0], p[1]),
      text: p => opts.rowText(p[0]) + ' ' + opts.chip(p[1]),
      emptyText: toggles.some(t => t.on)
        ? 'nothing matches: ' + toggles.filter(t => t.on).map(t => t.none || ('none ' + t.label))
            .join(', ')
        : 'nothing matches'});
    paintToggles();
    /* *** WHAT THE ROWS ARE ANSWERS TO, ABOVE THE ROWS. ***
       A group can carry a header -- the question a set of answers answered -- so the thing being
       measured is on screen rather than one click into a detail pane. */
    const gh = opts.groupHead && pickedGroup !== ALL ? opts.groupHead(pickedGroup) : null;
    head.replaceChildren(...[gh].filter(Boolean));
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
    detail.replaceChildren(...[].concat(opts.detailOf(row, g, pickedGroup === ALL)));
    detail.scrollTop = 0;
  }

  left.append(head, body);
  if (opts.groupSelect) { cols.className = 'wrap2 narrow'; cols.append(left, detail); }
  else cols.append(gnav, left, detail);
  /* No sentence above the columns: what the tab is, is the tab's own tip, and what a column
     means is that column's. */
  host.append(cols);
  pickSplit(pickedGroup);
  paintGroups();
  draw();
  /* Kept for the callers that flip a view from outside. */
  host.showGroups = () => { pickedGroup = withAll ? ALL : opts.groups[0]; facet = null; paintGroups(); draw(); };
  host.showRows = (g) => { pickedGroup = g; facet = null; paintGroups(); draw(); };
  /* a count elsewhere on the page lands here with exactly its rows */
  host.setToggles = keys => { for (const t of toggles) t.on = keys.includes(t.key);
                              pickedGroup = withAll ? ALL : opts.groups[0]; facet = null;
                              paintGroups(); draw(); };
  return host;
}

function conf(x) {
  if (x == null) return el('span', {class: 'tot', text: ''});
  /* Under the 0.6 gate an answer reports nothing as a finding, so the number is the point, and
     the cell says what it means rather than a sentence at the top of the tab saying it once. */
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
      text: 'assay could not read this model\u2019s SQL, so nothing below was checked.'}));
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

    /* *** THE BRANCH `dbt compile` NEVER RENDERS. *** (G-D) An incremental model's strategy,
       key, filter and schema-change setting, each with the badge of anything flagged on it. */
    const inc = m.incremental;
    if (inc) {
      const flag = k => ((inc.flags || {})[k] || []).flatMap(x => {
        const a = el('a', {class: 'lk plink', href: '#findings', text: x.check.replace(/_/g, ' ')});
        a.onclick = ev => { ev.preventDefault(); open('findings'); GO.findings(x.id); };
        return [badge('flagged', 'bad'), a];
      });
      const code = t => el('span', {class: 'mono'}, [wbr(t)]);
      const none = t => el('span', {class: 'tot', text: t});
      const rows = [
        ['strategy', el('span', {}, [el('span', {text: inc.strategy || 'not set'}),
          el('span', {class: 'tot', text: '  ' + (inc.strategy_from || '')})])],
        ['unique_key', el('span', {}, [inc.unique_key && inc.unique_key.length
          ? code(inc.unique_key.join(', ')) : none('none'), ...flag('unique_key')])],
      ];
      if (inc.strategy === 'microbatch')
        rows.push(['event_time', inc.event_time ? code(inc.event_time) : none('not set')],
                  ['lookback', el('span', {}, [el('span', {text: (inc.lookback == null
                    ? '1 (dbt’s default)' : String(inc.lookback)) + ' × ' + (inc.batch_size || '?')}),
                    ...flag('lookback')])]);
      else
        rows.push(['new rows are', el('span', {}, [inc.filter_sql ? code(inc.filter_sql)
          : none(inc.block ? (inc.filter_read ? 'filtered some other way' : 'a branch assay could not read')
                           : 'every row: no is_incremental() branch'),
          ...(inc.filter_sql ? [badge(inc.filter_lookback ? 'with a lookback' : 'no lookback',
                                      inc.filter_lookback ? '' : 'bad')] : []),
          ...flag('filter')])]);
      rows.push(['on_schema_change', el('span', {}, [inc.on_schema_change ? code(inc.on_schema_change)
        : none('ignore (the default)'), ...flag('on_schema_change')])]);
      d.append(section('incremental', kv(rows), 'How this model loads after its first build. '
        + 'The compiled SQL is the full refresh; this is the other branch, read from the raw code.'));
    }

    /* *** PROVEN. *** (L2) Each certificate about this model, what it rests on, and whether
       that still holds. */
    const pr = PROOFS_BY_MODEL[m.uid] || [];
    /* *** EVERY LINE NAMED THE MODEL WHOSE PANE IT IS, AND THE SAME LINE CAME THREE TIMES. ***
       In its own pane a certificate says "its rows"; identical ones (three joins onto one CTE)
       read once with a count; what needs attention comes first; and a broken premise is said
       once, not as "as long as X ... Not so: X is not so". */
    const groupsP = [];
    for (const r of [...pr].sort((a, b) => (PROOF_ORDER[a.guarantee] ?? 9)
                                           - (PROOF_ORDER[b.guarantee] ?? 9))) {
      const key = ownStatement(r) + '\u001f' + r.guarantee + '\u001f' + (r.lost_because || '')
        + '\u001f' + (provedIt(r) ? '' : r.missing || '');
      const g = groupsP.find(x => x.key === key);
      if (g) g.n += 1; else groupsP.push({key, r, n: 1});
    }
    if (pr.length) d.append(section('proven (' + pr.filter(r => r.status === 'proven').length
      + ' of ' + pr.length + ')', el('div', {class: 'ulist'}, groupsP.map(({r, n}) =>
        el('div', {class: 'urow'}, [
        el('span', {}, [wbr(ownStatement(r) + (n > 1 ? ' (' + n + ' times)' : ''))]), gbadge(r),
        el('div', {class: 'usub'}, [wbr(proofSub(r))])]))),
      'Checked by Lean from the parsed structure: each holds for every input its premises allow.'));

    /* *** CORRECT AS LONG AS. *** Every premise something about this model rests on, one line
       each with its status, linked to Guarantees. Omitted when there are none. */
    /* premises only a certificate above rests on are already said there */
    const prem = (PREM_BY_MODEL[m.uid] || []).filter(p => (p.uses || []).some(u =>
      u.model === m.uid && u.kind !== 'proof'));
    if (prem.length) d.append(section('correct as long as', el('div', {class: 'ulist'},
      prem.map(p => {
        const a = el('a', {class: 'lk', href: '#guarantees'}, [wbr(p.statement)]);
        a.onclick = ev => { ev.preventDefault(); open('guarantees'); GO.guarantees(p.id); };
        const why = (p.uses || []).filter(u => u.model === m.uid)
          .map(u => u.kind === 'grain' ? 'its grain' : u.kind === 'proof' ? 'a proof'
            : u.dependent.split(':')[0].replace(/_/g, ' '));
        return el('div', {class: 'urow'}, [a, premBadge(p.status, p.label, p.why),
          el('div', {class: 'usub', text: 'for ' + [...new Set(why)].join(', ')})]);
      })), 'The statements about the data that what assay says about this model leans on. If '
        + 'one stops being true, the finding it held back is raised again.'));

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
           : (f.ruled_model ? 'model ruled' : 'unread')})},
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
           : badge('contradicts', 'bad', c.contradicted)},
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

  host.replaceChildren(el('div', {class: 'wrap2'}, [list, detail]));
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
    {key: 'in', label: 'reads', n: 1, val: m => (EDGES_IN[m.uid] || []).length,
     tip: 'Models and sources this one reads from.'},
    {key: 'out', label: 'read by', n: 1, val: m => (EDGES_OUT[m.uid] || []).length,
     tip: 'Models that read this one.'},
    {key: 'note', label: 'notable', n: 1, val: m => nOf(m),
     tip: 'Hops into this model worth a look: a join carrying no key assay could resolve, more '
       + 'than 60 columns dropped, or under half the parent\u2019s rows kept. Each is named under '
       + 'the drawing. ' + DATA.edges.filter(e => why(e)).length + ' of ' + num(DATA.edges.length)
       + ' hops in this project.',
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

  host.replaceChildren(el('div', {class: 'wrap2'}, [list, detail]));
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
     tip: 'Whether the SQL contradicts the sentence, and how sure that reading is. A dash means '
       + 'not checked yet.',
     cell: c => c.contradicted == null ? el('span', {class: 'tot', text: '\u2014'})
       : el('span', {class: 'bad', text: c.contradicted.toFixed(2)})},
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
               none: 'the code contradicts no claim about this model',
               title: 'only claims a judgment read against the SQL and found contradicted',
               where: c => c.contradicted != null}],
    rowFilter: 'filter claims...',
    rowsOf: g => g.rows, rowCols: rowCols, rowSort: 'v', rowDir: -1,
    rowText: c => [c.text, c.source_ref, c.kind].join(' '),
    /* The claim itself is a SENTENCE, and a sentence in a table cell is a sentence you skim.
       The right pane is where it gets read. */
    detailOf: c => pane({
      kind: 'claim \u00b7 ' + (c.kind || 'unclassified').replace(/_/g, ' '),
      title: link(c.subject_name),
      where: el('span', {class: 'mono', text: c.source_ref || ''}),
      what: el('span', {class: 'quote'}, [wbr(c.text)]),
      reading: [section('the code', c.contradicted == null
        ? el('p', {class: 'prose', text: 'Not checked. No question has read the SQL against this '
            + 'sentence yet.'})
        : el('p', {class: 'prose'}, [badge('contradicts', 'bad', c.contradicted),
            el('span', {text: ' The SQL was read against this sentence and they disagree.'})]))],
    }),
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
  /* In the list a reading is its short name; the pane has it in full. A badge never wraps, so a
     long one in a narrow column pushed the table past its pane at 1100px. */
  const SHORT = {one_rule_repeated: 'one rule', independent_decisions: 'independent',
                 deliberate_exception: 'deliberate', undeclared_divergence: 'undeclared',
                 in_a_shared_macro: 'shared macro', at_the_source: 'at the source',
                 upstream_in_one_model: 'upstream'};
  const readShort = r => r ? badge(SHORT[r.answer] || r.answer.replace(/_/g, ' '), 'judged')
                           : el('span', {class: 'tot', text: 'not asked'});
  const read = r => r ? badge(r.answer.replace(/_/g, ' '), 'judged', r.confidence)
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
       {key: 'size', label: 'models', n: 1, val: c => c.size,
        tip: 'How many models write this filter, with the column names taken out.'},
       {key: 'shape', label: 'filter', mono: 1, clip: 1, val: c => c.shape},
       {key: 'one', label: 'one rule?', val: c => c.one_rule ? c.one_rule.answer : '',
        tip: 'Whether the models apply one rule, repeated, or each made its own decision. Read '
          + 'by `assay clusters --judge`; the grouping itself is exact and cost nothing. '
          + '"written once" means a macro already holds it.',
        cell: c => c.macro_at ? badge('one macro', 'declared') : readShort(c.one_rule)}],
     sort: 'size', dir: -1},
    {key: 'odd', label: 'the one that differs', rows: odds,
     cols: [
       {key: 'model', label: 'model', mono: 1, val: o => o.model, cell: o => link(o.model)},
       {key: 'diff', label: 'what differs', clip: 1, val: o => o.difference},
       {key: 'read', label: 'read as', val: o => o.read_as ? o.read_as.answer : '',
        tip: 'Whether the difference is a deliberate exception or a divergence nobody declared. '
          + 'Read by `assay clusters --judge`.',
        cell: o => readShort(o.read_as)}],
     sort: 'model', dir: 1},
    {key: 'same', label: 'one claim, several models', rows: same,
     cols: [
       {key: 'n', label: 'models', n: 1, val: g => g.length,
        tip: 'How many models the project makes this one claim about.'},
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
    detailOf: r => {
      const k = kind(r);
      if (k === 'filters') return pane({
        kind: 'filter written the same way \u00b7 ' + r.models.length + ' models',
        title: [wbr(r.shape)], mono: 1,
        where: models(r.models),
        what: r.macro_at ? 'Written once, in ' + r.macro_at + '.' : 'Each model writes it itself.',
        reading: [section('the reading', kv([
          ['one rule?', r.macro_at ? el('span', {class: 'tot', text: 'settled: it is one macro'})
                                   : read(r.one_rule)],
          ['fix belongs', r.macro_at ? el('span', {class: 'tot', text: '\u2014'})
                                     : read(r.fix_belongs)]]))]});
      if (k === 'odd') return pane({
        kind: 'the one that differs',
        title: link(r.model),
        where: el('span', {}, [el('span', {class: 'tot', text: 'the others: '}),
                               models(r.shared_by || [])]),
        what: r.difference,
        reading: [section('what it writes', el('pre', {text: r.this || ''})),
                  section('what the others write', el('pre', {text: r.shared || ''})),
                  section('the reading', kv([['read as', read(r.read_as)]]))]});
      /* The members' own words: the claim is "the same" by a judgment, not by string equality,
         so each wording is shown as it was written -- once, with every model that wrote it,
         because ten models writing one sentence is one wording and not ten. */
      const byText = {};
      for (const x of r) (byText[x.claim] = byText[x.claim] || []).push(x.model);
      const words = Object.entries(byText);
      return pane({
        kind: 'one claim, several models',
        title: r.length + ' models',
        where: models([...new Set(r.map(x => x.model))]),
        what: words.length === 1 ? el('span', {class: 'quote'}, [wbr(words[0][0])])
                                 : words.length + ' wordings of one claim',
        reading: words.length === 1 ? [] : [section('each wording', el('div', {}, words.map(
          ([t, ms]) => el('div', {class: 'opt'}, [el('p', {class: 'quote'}, [wbr(t)]),
                                                  models(ms)]))))]});
    },
  }));
}

/* ----------------------------------------------------------------------------- Findings */
/* *** "weight 7.6" WITH NO FORMULA. ***
   The parts come from `Finding.weight_parts()`, the same function the weight itself is computed
   from, so what this shows cannot disagree with the order the list is in. */
const nf = x => (Math.round(x * 100) / 100).toString();
function weightTerms(f) {
  const w = f.weight_parts;
  if (!w) return null;
  const c = w.counts, cap = w.caps, dv = w.divisors;
  const capped = k => c[k] > cap[k] ? ', counted as ' + cap[k] : '';
  return [
    [nf(w.parts.descendants), num(c.descendants) + ' descendant(s)' + capped('descendants')
      + ' \u00f7 ' + dv.descendants],
    [nf(w.parts.marts), num(c.marts) + ' mart(s)' + capped('marts') + ' \u00f7 ' + dv.marts],
    [nf(w.parts.exposures), num(c.exposures) + ' exposure(s)' + capped('exposures')
      + ' \u00d7 ' + w.per_exposure],
  ];
}
function weightLine(f) {
  const w = f.weight_parts, t = weightTerms(f);
  if (!t) return 'weight ' + f.weight;
  return 'base ' + nf(w.base) + ' \u00d7 (1 + ' + t.map(x => x[0]).join(' + ') + ') = '
    + nf(w.base) + ' \u00d7 ' + nf(w.lift) + ' = ' + nf(f.weight) + '\n'
    + t.map(x => x[0] + ' from ' + x[1]).join('\n');
}
function weightBox(f) {
  const w = f.weight_parts, t = weightTerms(f);
  if (!t) return el('span', {text: String(f.weight)});
  const box = el('div', {class: 'wbox'});
  box.append(el('div', {}, [el('span', {text: nf(f.weight)}), el('span', {class: 'tot',
    text: '  =  '}), el('span', {class: 'tot', text: 'base ' + nf(w.base),
    tip: 'The check\u2019s own severity, before reach.'}), el('span', {class: 'tot',
    text: ' \u00d7 '}), el('span', {class: 'tot', text: 'lift ' + nf(w.lift),
    tip: 'Reach. An exposure outweighs the whole marts-and-descendants lift, which tops out at '
      + '4, so what reaches a product ranks above what reaches a layer.'})]));
  const tbl = el('div', {class: 'wterms'});
  tbl.append(el('span', {class: 'n', text: '1'}), el('span', {class: 'tot', text: 'every finding'}));
  for (const [v, why] of t)
    tbl.append(el('span', {class: 'n', text: '+ ' + v}), el('span', {text: why}));
  box.append(tbl);
  return box;
}

/* *** ONE SHAPE FOR EVERY DETAIL PANE. *** (G4)
   Claims, Areas, Answers and Findings each put their own headings in their own order, so every
   tab had to be learned separately. Every pane is now: what kind of thing it is, its name, where
   it is, the one line that says it, the reading under headings, and what to do about it last. */
function pane(o) {
  const out = [];
  if (o.kind) out.push(el('div', {class: 'pkind', text: o.kind}));
  out.push(el('h2', {class: o.mono ? 'mono' : ''}, [].concat(o.title)));
  if (o.where) out.push(el('div', {class: 'path'}, [].concat(o.where)));
  if (o.what) out.push(el('p', {class: 'plead'}, [].concat(o.what)));
  for (const r of (o.reading || [])) if (r) out.push(r);
  if (o.act) out.push(section('what to do', o.act));
  return out;
}

/* *** FOUR FACTS IN ONE SENTENCE WAS NOT READ. *** (Ryan) What the form shows, what it leaves
   out and why, and what the evaluator's rows became: one labelled line each. */
function tallyGrid(T, onPage) {
  if (!T) return null;
  const rows = [];
  const add = (label, value, note) => rows.push(el('span', {class: 'tl', text: label}),
    el('span', {}, [el('b', {text: value}), note ? el('span', {class: 'tn', text: ' ' + note}) : null]
      .filter(Boolean)));
  /* *** 2,202 NOTES ARE NOT 2,202 PROBLEMS. *** (Ryan) What the policy queues leads, split into
     broken now and worth a look; the notes are one line; no verdict on the warehouse. */
  if (T.queued != null) {
    const paid = n => n ? '(' + num(n) + ' customer-facing)' : '';
    add('broken now', num(T.broken), paid(T.broken_paid));
    add('worth a look', num(T.look), paid(T.look_paid));
    add('notes', num(T.notes), 'not queued by audit.yml; inside the changes that clear them, '
        + 'and in Explore');
    if (!onPage) add('to decide', num(T.cards) + ' cards in ' + num(T.groups) + ' groups',
                     'the queued judgment calls; one verdict per group, and any card can differ');
  } else {
    if (onPage) add('open', num(T.findings) + ' findings', 'on ' + num(T.pairs) + ' model + check pairs');
    add(onPage ? 'on the review form' : 'to rule on', num(T.cards) + ' cards',
        'one card is one model and one check; you can split a card if its findings differ');
  }
  if (T.ruled_pairs) add('already ruled', num(T.ruled_pairs) + ' cards (' + num(T.ruled_findings)
      + ' findings)', onPage ? 'not on the form; still listed here until fixed'
                             : 'left off this form; still open on the page until fixed');
  const aside = Object.entries(T.set_aside || {}).filter(([, n]) => n)
    .map(([k, n]) => num(n) + ' ' + k);
  if (aside.length) add('set aside', aside.join(' · '), 'not open, so on neither list');
  return el('div', {class: 'tgrid'}, rows);
}

function findingsTab(host) {
  /* *** ONE ROW PER MODEL. *** (Ryan) A model with seven findings was seven rows, each repeating
     its reason and its weight, marts and feeds. The list is the models now, with how many
     findings each has and the worst of them; the findings, their reasons and their numbers are
     in the detail. The checks are a dropdown in the filter bar, and the counts at the top of
     the page switch the filters on. */
  const RANK = new Map(DATA.findings.map((f, i) => [f.id, i]));
  const ORDER = {broken: 0, look: 1, note: 2};
  const LABEL = {broken: 'broken now', look: 'worth a look', note: 'note'};
  const byModel = {};
  for (const f of DATA.findings) {
    const k = f.subject || f.model || f.check;
    const m = byModel[k] = byModel[k] || {key: k, model: f.model || k, findings: []};
    m.findings.push(f);
  }
  const models = Object.values(byModel);
  for (const m of models) {
    m.findings.sort((a, b) => (ORDER[a.bucket] ?? 3) - (ORDER[b.bucket] ?? 3)
                              || RANK.get(a.id) - RANK.get(b.id));
    m.worst = m.findings[0].bucket || '';
    // broken now first, then worth a look, then notes; within each, the one priority
    m.rank = (ORDER[m.worst] ?? 3) * 1e7 + Math.min(...m.findings.map(f => RANK.get(f.id)));
  }
  const byCheck = {};
  for (const f of DATA.findings) {
    const g = byCheck[f.check] = byCheck[f.check] || {check: f.check, title: f.title || f.check,
                                                      rows: new Set()};
    g.rows.add(byModel[f.subject || f.model || f.check]);
  }
  const groups = Object.values(byCheck).map(g => ({...g, rows: [...g.rows]}));
  const has = (m, pred) => m.findings.some(pred);
  const FPRED = {broken: f => f.bucket === 'broken', look: f => f.bucket === 'look',
                 note: f => f.bucket === 'note', paid: f => f.paid && f.bucket !== 'note'};
  const fcount = (rows, g, keys) => rows.reduce((n, m) => n + m.findings.filter(f =>
    (!g || f.check === g.check) && (keys || []).every(k => FPRED[k](f))).length, 0);
  const toggles = [
    {key: 'broken', label: 'broken now', where: m => has(m, f => f.bucket === 'broken')},
    {key: 'look', label: 'worth a look', where: m => has(m, f => f.bucket === 'look')},
    {key: 'note', label: 'notes', where: m => has(m, f => f.bucket === 'note')},
    {key: 'paid', label: 'customer-facing', where: m => has(m, f => f.paid && f.bucket !== 'note')},
  ].map(t => ({...t, count: models.filter(t.where).length}));
  const d = drill({
    noun: 'models', groups: groups, chip: g => g.title, groupSelect: true, groupNoun: 'check',
    /* *** EVERY COUNT ON THE SCREEN NAMES ITS UNIT. *** (Ryan: "findings shows 2183 but i can
       only see 379") The rows are models; the dropdown, the filters and the tab count findings,
       and the list's line says both. */
    countUnit: 'findings', countOf: (rows, g, keys) => fcount(rows, g, keys),
    countLine: (rows, from, to, g, keys) => num(from) + '\u2013' + num(to) + ' of '
      + num(rows.length) + (rows.length === 1 ? ' model' : ' models') + ' \u00b7 '
      + num(fcount(rows, g, keys)) + ' findings',
    groupText: g => g.check, allRows: () => models, toggles: toggles,
    rowsOf: g => g.rows,
    rowCols: [
      {key: 'model', label: 'model', mono: 1, val: m => m.model, cell: m => link(m.model)},
      {key: 'n', label: 'findings', n: 1, val: m => m.findings.length},
      {key: 'p', label: 'worst', val: m => m.rank,
       tip: 'Broken now, then worth a look, then notes; within each, customer-facing and what '
         + 'is wrong now before reach.',
       cell: m => el('span', {class: 'tot', style: 'white-space:nowrap',
                              text: LABEL[m.worst] || ''})}],
    rowSort: 'p', rowDir: 1, rowFilter: 'filter by model or text...',
    rowText: m => m.model + ' ' + m.findings.map(f => f.check + ' ' + f.summary).join(' '),
    detailOf: m => {
      const out = [el('h2', {class: 'dh mono', text: m.model})];
      for (const f of m.findings) {
        const box = el('div', {class: 'fblock', id: 'f-' + f.id});
        box.append(el('div', {class: 'fbucket', text: (LABEL[f.bucket] || '')
          + (f.paid ? ' \u00b7 customer-facing' : '')}));
        box.append(...[].concat(findingPane(f)));
        out.push(box);
      }
      return out;
    },
  });
  host.replaceChildren(d);
  GO.findings = id => {
    const f = FIND[id]; if (!f) return;
    d.showRows(byCheck[f.check] ? groups.find(g => g.check === f.check) : undefined);
    const key = f.subject || f.model || f.check;
    for (const tr of host.querySelectorAll('tbody tr')) {
      const r = Array.isArray(tr._row) ? tr._row[0] : tr._row;
      if (r && r.key === key) {
        tr.click(); if (tr.scrollIntoView) tr.scrollIntoView({block: 'nearest'});
        const b = document.getElementById('f-' + id);
        if (b && b.scrollIntoView) b.scrollIntoView({block: 'start'});
        return;
      }
    }
  };
  GO.findingsFilter = keys => d.setToggles(keys);
}

/* *** A FINDING HELD BACK ON A PREMISE, AND RAISED WHEN IT BROKE. *** The premise, its badge,
   the day it broke and what measured it, above everything else in the pane, because it is the
   reason the finding is on the list at all. */
function backBlock(b) {
  if (!b) return null;
  const prem = el('span', {}, [wbr(b.premise || ''), premBadge(b.status, b.status, b.why)]);
  if (b.premise_id && (DATA.premises || []).some(p => p.id === b.premise_id)) {
    const a = el('a', {class: 'lk plink', text: 'in Guarantees', href: '#guarantees'});
    a.onclick = ev => { ev.preventDefault(); open('guarantees'); GO.guarantees(b.premise_id); };
    prem.append(a);
  }
  return section('why it is back', kv([
    ['premise', prem],
    ['broke', b.broke_on || null],
    ['measured', b.why ? wbr(b.why) : null],
    ['held back while', b.held_back ? wbr(b.held_back) : null]]));
}

/* A commit, linked when the repository has a web remote, short either way. */
function commitLink(sha) {
  if (!sha) return el('span', {class: 'tot', text: 'no commit recorded'});
  const short = String(sha).replace('+dirty', '').slice(0, 9)
    + (String(sha).includes('+dirty') ? ' with uncommitted changes' : '');
  const url = (DATA.meta || {}).repo_url;
  if (!url) return el('span', {class: 'mono', text: short});
  return el('a', {class: 'mono lk', href: url + '/commit/' + String(sha).replace('+dirty', ''),
                  target: '_blank', rel: 'noopener', text: short});
}

/* *** FIXED, AND BACK. *** (G-B) When it was agreed, where it was gone, where it came back. */
function timelineBlock(f) {
  const t = (f.evidence || {}).timeline;
  if (f.check !== 'fixed_finding_returned' || !t) return null;
  const orig = el('span', {class: 'mono', text: (f.evidence || {}).returned || ''});
  let now = null;
  if (t.finding_now && FIND[t.finding_now]) {
    now = el('a', {class: 'lk mono', href: '#findings', text: t.finding_now});
    now.onclick = ev => { ev.preventDefault(); GO.findings(t.finding_now); };
  }
  return section('what happened', kv([
    ['agreed by', el('span', {text: (t.agreed_by || 'a person') + (t.agreed_at ? ', ' + t.agreed_at : '')})],
    ['agreed finding', orig],
    ['gone', el('span', {}, [el('span', {text: (t.gone_at || '') + ' at '}), commitLink(t.gone_commit),
      el('span', {class: 'tot', text: '  run ' + (t.gone_run || '')})])],
    ['back', el('span', {}, [el('span', {text: (t.back_at || 'this run') + ' at '}),
      commitLink(t.back_commit),
      ...(t.back_run && t.back_run !== 'now' ? [el('span', {class: 'tot', text: '  run ' + t.back_run})] : [])])],
    ['the finding now', now],
  ]), 'Agreed real by a person, gone after the model’s file changed, and here again.');
}

function findingPane(f) {
  const back = (f.evidence || {}).why_it_is_back;
  const ev = Object.assign({}, f.evidence || {});
  delete ev.why_it_is_back;
  delete ev.timeline;
  delete ev.proven_rule;
  return pane({
    kind: 'finding · ' + f.check.replace(/_/g, ' '),
    title: link(f.model), mono: 0,
    where: el('span', {class: 'mono', text: f.file || ''}),
    what: f.summary,
    reading: [
      backBlock(back),
      timelineBlock(f),
      /* A float sum's fix is one expression, so it is shown as one. (G-C) */
      f.check === 'float_sum_is_not_reproducible' && ev.recommendation
        ? section('the column', kv([['column', el('span', {class: 'mono', text: ev.column})],
            ['adds up', el('span', {class: 'mono', text: ev.aggregate + '(' + ev.input + ')'})],
            ['its type', el('span', {}, [el('span', {class: 'mono', text: ev.input_type}),
              el('span', {class: 'tot', text: '  from ' + (ev.type_from || '').replace(/`/g, '')})])],
            ['write instead', el('code', {class: 'tick', text: ev.recommendation})]]))
        : null,
      ...((f.evidence || {}).asked ? [section('the reading', kv([
        ['asked', String(f.evidence.asked).replace(/_/g, ' ')
          + (f.evidence.context && f.evidence.context !== f.model ? ', about ' + f.evidence.context : '')],
        ['answered', String(f.evidence.answer || '')],
        ['sure', f.evidence.probability == null ? '' : Number(f.evidence.probability).toFixed(2)]]))] : []),
      section((f.evidence || {}).asked ? 'why that is a finding' : 'what it means', md(f.detail || '')),
      section('how much it matters', kv([
        ['weight', weightBox(f)],
        ['reaches', (f.exposures || []).length ? reachesBox(f.exposures)
          : el('span', {class: 'tot', text: 'no exposure'})],
        ['marts downstream', num(f.marts)],
        ['descendants', num(f.descendants)],
        ...(backsLine((f.evidence || {}).context) ? [['the test backs',
            backsLine((f.evidence || {}).context)]] : []),
        ...(f.group ? [['same construct', el('span', {text: f.group.size + ' models (' +
            f.group.models.slice(0, 5).join(', ') + (f.group.size > 5 ? ', ...' : '') + '), ' +
            (f.group.macro_at ? 'one edit in ' + f.group.macro_at : 'written inline in each')})]]
          : []),
        /* First SEEN by a full check, at the commit HEAD was on. `backtest` finds the commit
           that introduced it. */
        ...(f.first_seen ? [['first seen', el('span', {text: f.first_seen.at +
            (f.first_seen.commit ? ' at ' + f.first_seen.commit : '')})]] : []),
      ])),
      section('who decided it', kv([
        ['found by', f.rests_on ? badge('the ' + f.rests_on.replace(/_/g, ' ') + ' question',
                                        'judged')
                                : badge('the SQL parser', 'declared')],
        /* L1: the rule this check applies is a theorem in assay's Lean library. */
        ...((f.evidence || {}).proven_rule ? [['the rule', (() => {
          const b = badge('proven rule', 'proven');
          b.setAttribute('data-tip', 'This rule is proven in Lean (`' + f.evidence.proven_rule
            + '`): it holds for every input that satisfies its premises. The finding depends only '
            + 'on the parsed structure and the premises listed.');
          return el('span', {}, [b, el('span', {class: 'mono tot', text: '  ' + f.evidence.proven_rule})]);
        })()]] : []),
        ['read by a person', f.ruled_finding ? badge('yes', 'on')
          : f.ruled_model ? el('span', {text: 'the model was ruled on, this finding was not'})
          : el('span', {class: 'tot', text: 'nobody yet'})],
      ])),
      section('evidence', kvAny(ev)),
    ],
    act: el('div', {}, [
      el('p', {class: 'prose', text: 'Rule on it in the review form, or from an agent:'}),
      el('pre', {text: "rule(finding='" + f.id + "', verdict='agree' | 'disagree' | 'accept', "
                       + "why='...')"})]),
  });
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

/* What an answer was about, in words: the model, and the part of it the question named. A
   column question names the column in its id (`role__addr_key`); a sentence, an edge or a
   monitor carries it in the context, which for a sentence starts with the model's own name. */
function answerPart(a) {
  const s = subjectOf(a);
  const q = String(a.question || '');
  const ctx = String(a.context || '');
  let part = ctx.startsWith(s.name + ': ') ? ctx.slice(s.name.length + 2) : ctx;
  if (part === s.name) part = '';
  if (!part && q.includes('__') && !/^\d+$/.test(q.split('__')[1])) part = q.split('__')[1];
  return {model: s.name, scope: s.scope, part: part};
}

/* *** A QUESTION'S WORDS WITH ITS FIELD NAMES IN THEM. *** (N2) "`model` has `marts_downstream`
   marts reading it" was the question header. Filled from one answer where there is one (the model,
   and its reach), and in general words where there is not. */
const FIELD_WORDS = {model: 'the model', marts_downstream: 'N', models_downstream: 'N',
                     descendants: 'N', test: 'a test', monitor: 'a monitor', column: 'a column',
                     source: 'the source'};
function fillQuestion(text, a) {
  const m = a ? BY_NAME[subjectOf(a).name] : null;
  const part = a ? answerPart(a).part : '';
  const vals = m ? {model: m.name, marts_downstream: m.marts, models_downstream: m.descendants,
                    descendants: m.descendants} : {};
  let unknownUsed = false;
  return String(text || '').replace(/`([a-z][a-z0-9_]*)`/g, (all, f) => {
    if (vals[f] != null) return vals[f] === m.name ? '`' + m.name + '`' : String(vals[f]);
    if (a && part && !unknownUsed && !(f in vals)) { unknownUsed = true; return '`' + part + '`'; }
    return FIELD_WORDS[f] || f.replace(/_/g, ' ');
  });
}

function answersTab(host) {
  const fam = {};
  for (const a of DATA.decisions) {
    const p = a.question.split('__')[0];
    const g = fam[p] = fam[p] || {prefix: p, rows: [], low: 0, found: 0};
    g.rows.push(a);
    if (a.confidence != null && a.confidence < 0.6) g.low++;
    if (a.finding) g.found++;
  }
  const qByPrefix = {};
  for (const q of DATA.questions) if (q.id_prefix) qByPrefix[q.id_prefix] = q.name;
  const groups = Object.values(fam);
  const qOf = g => (DATA.questions || []).find(x => x.id_prefix === g.prefix);
  const words = g => { const q = qOf(g); return q && (q.instructions || {}).question; };

  host.replaceChildren(drill({
    noun: 'answers', groups: groups, rowFilter: 'filter by model or text...',
    chip: g => qByPrefix[g.prefix] || g.prefix,
    groupFilter: 'find a question...',
    groupSub: g => g.found ? num(g.found) + ' became findings' : null,
    /* It opens on the question whose answers produced the most findings: the answers somebody
       came here to check. */
    startGroup: gs => gs.slice().sort((a, b) => b.found - a.found || b.rows.length - a.rows.length)[0],
    split: {of: a => a.finding ? 'found' : 'none', choices: [
      ['found', 'became a finding', 'Answers that produced a finding on the Findings tab.'],
      ['none', 'no finding', 'Answers that produced no finding: the answer was not a defect, or '
        + 'the model was under 0.60 and could not tell.']]},
    facet: {label: 'answer', of: a => a.answer == null ? '(no answer)' : a.answer},
    /* The question, once, above its answers. */
    groupHead: g => el('div', {class: 'qhead'}, [
      el('p', {class: 'quote', text: fillQuestion(words(g)) || 'This question is no longer in the bank; its '
        + 'answers are kept as they were given.'})]),
    rowsOf: g => g.rows,
    rowCols: [
      {key: 'about', label: 'asked about', val: a => answerPart(a).model + ' ' + answerPart(a).part,
       tip: 'The model the question was asked about, and the part of it: a column, a sentence, a '
         + 'hop or a test.',
       cell: a => { const x = answerPart(a);
         return el('div', {}, [BY_NAME[x.model] ? link(x.model) : el('span', {class: 'mono'},
             [wbr(x.model)]),
           ...(x.part ? [el('div', {class: 'apart'}, [wbr(x.part)])] : [])]); }},
      {key: 'a', label: 'answer', val: a => a.answer, cell: a => el('span', {}, [wbr(a.answer)])},
      {key: 'c', label: 'sure', n: 1, val: a => a.confidence, cell: a => conf(a.confidence),
       tip: 'How confident the answer was, from 0 to 1. Under 0.60 nothing becomes a finding.'},
    ],
    rowSort: 'c', rowDir: -1,
    rowText: a => [a.question, a.key, a.context, a.answer].join(' '),
    detailOf: (a, g, all) => {
      const x = answerPart(a);
      const f = a.finding ? FIND[a.finding] : null;
      const q = qOf(g || {prefix: a.question.split('__')[0]});
      return pane({
        kind: 'answer · ' + (qByPrefix[a.question.split('__')[0]] || a.question).replace(/_/g, ' '),
        title: [wbr(a.answer == null ? '(no answer)' : a.answer)],
        where: [BY_NAME[x.model] ? link(x.model) : el('span', {class: 'mono', text: x.model}),
                ...(x.scope ? [badge(x.scope)] : [])],
        what: x.part ? [wbr(x.part)] : null,
        reading: [
          ...(all && q && (q.instructions || {}).question
            ? [section('the question', el('p', {class: 'quote', text: fillQuestion(q.instructions.question, a)}))]
            : []),
          section('how sure', kv([
            ['sure', a.confidence != null && a.confidence < 0.6
              ? el('span', {}, [conf(a.confidence), el('span', {class: 'low', text: '  not reported',
                  tip: 'Under 0.60 nothing becomes a finding: the model could not tell, usually '
                    + 'because what it was given does not carry the answer.'})])
              : conf(a.confidence)],
            ['next best', a.runner_up ? a.runner_up[0] + ' at ' + a.runner_up[1].toFixed(2)
                                      : 'nothing else scored'],
            ['asked with', a.prompt_version || 'no recorded version'],
          ])),
          section('what came of it', f
            ? el('div', {}, [el('p', {class: 'prose'}, [md(f.summary || '')]), goFinding(f.id)])
            : el('p', {class: 'prose', text: 'No finding.'})),
        ],
      });
    },
  }));
}

/* A link that opens the Findings tab on one finding: its check picked, its row selected. */
function goFinding(id) {
  const a = el('a', {class: 'lk', href: '#', text: 'open the finding'});
  a.onclick = ev => { ev.preventDefault(); open('findings'); (GO.findings || (() => {}))(id); };
  return a;
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
/* What each bank is about, in words. A bank this project wrote is named as its own. */
const BANK_TOPIC = {align: 'joins and grain', clusters: 'repeated filters and claims',
  columns: 'what columns are', feeds: 'source fields', grain: 'keys', meaning: 'what values mean',
  practices: 'dbt practices', reads: 'reading findings', rows: 'failing rows',
  rulings: 'rulings', semantics: 'descriptions and claims', testing: 'tests',
  volume: 'monitoring'};
const bankLabel = q => q.yours ? 'yours: ' + (q.bank || '').replace(/_/g, ' ')
                               : (BANK_TOPIC[q.bank] || (q.bank || 'other').replace(/_/g, ' '));

function questionsTab(host) {
  /* *** A "FAMILY" WAS ONE QUESTION. *** (P5)
     Every entry here is one question, and calling it a family sent a reader looking for a group
     that was not there. The real groups are the banks the questions are written in -- what
     values mean, monitoring, descriptions and claims, this project's own -- so those are the
     groups on the left, and each question shows its text, what it is asked about, and what came
     back. */
  const verdicts = {};
  for (const a of DATA.adjudications) {
    const k = a.family || ''; verdicts[k] = verdicts[k] || {human: 0, all: 0};
    verdicts[k].all++; if (a.source === 'human') verdicts[k].human++;
  }
  const answersOf = {};
  for (const d of DATA.decisions)
    (answersOf[d.question.split('__')[0]] = answersOf[d.question.split('__')[0]] || []).push(d);
  const byBank = {};
  for (const q of DATA.questions) {
    const k = bankLabel(q);
    (byBank[k] = byBank[k] || {label: k, yours: q.yours, rows: []}).rows.push(q);
  }
  const groups = Object.values(byBank).sort((a, b) =>
    (a.yours - b.yours) || b.rows.length - a.rows.length || a.label.localeCompare(b.label));
  const asked = q => (answersOf[q.id_prefix] || []).length;
  const human = q => (verdicts[q.name] || {}).human || 0;

  host.replaceChildren(drill({
    noun: 'questions', groups: groups, chip: g => g.label, groupFilter: 'find a topic...',
    keepOrder: 1,
    rowsOf: g => g.rows,
    rowCols: [
      {key: 'name', label: 'question', mono: 1, val: q => q.name},
      {key: 'asked', label: 'asked', n: 1, val: q => asked(q),
       tip: 'How many things in this project this question has a live answer about.'},
      {key: 'human', label: 'human', n: 1, val: q => human(q),
       tip: 'Verdicts a person recorded on the findings this question produced. Only these let '
         + 'a check fail a build.',
       cell: q => el('span', {class: human(q) ? 'ok' : 'tot', text: num(human(q))})},
    ],
    rowSort: 'asked', rowDir: -1, rowFilter: 'filter questions...',
    rowText: q => [q.name, q.bank, JSON.stringify(q.instructions)].join(' '),
    detailOf: q => {
      const ins = q.instructions || {};
      const got = answersOf[q.id_prefix] || [];
      const counts = {};
      for (const d of got) counts[d.answer] = (counts[d.answer] || 0) + 1;
      const crit = q.criteria || {};
      const opts = el('div');
      for (const name of Object.keys(crit).sort()) {
        const c = crit[name], blk = el('div', {class: 'opt'});
        blk.append(el('div', {class: 'optname'}, [el('span', {class: 'mono', text: name}),
          el('span', {class: 'tot', text: '  ' + num(counts[name] || 0) + ' answered this'})]));
        if (typeof c === 'string') blk.append(el('p', {class: 'prose', text: c}));
        else {
          if (c.what) blk.append(el('p', {class: 'prose', text: c.what}));
          if (c.not_for) blk.append(el('p', {class: 'prose'},
            [el('span', {class: 'tot', text: 'not for: '}), el('span', {text: String(c.not_for)})]));
          if ((c.examples || []).length)
            blk.append(el('div', {class: 'chips flat'},
              c.examples.map(x => el('span', {class: 'chip mono', text: String(x)}))));
        }
        opts.append(blk);
      }
      const extra = Object.keys(ins).filter(k => k !== 'question').sort();
      return pane({
        kind: 'question · ' + bankLabel(q),
        title: [wbr(q.name)],
        where: el('span', {class: 'mono', text: [q.prompt_version, q.yours ? 'written in this '
          + 'project' : 'ships with assay'].filter(Boolean).join('  ·  ')}),
        what: el('span', {class: 'quote', text: fillQuestion(ins.question)}),
        reading: [
          ...(extra.length ? [section('how it is asked', el('div', {}, extra.map(k =>
            el('p', {class: 'prose'}, [el('span', {class: 'tot', text: k + ': '}),
                                       el('span', {text: String(ins[k])})]))))] : []),
          section('on this project', kv([
            ['asked about', num(got.length) + (got.length === 1 ? ' subject' : ' subjects')],
            ['became findings', num(got.filter(d => d.finding).length)],
            ['verdicts', num(human(q)) + ' by a person' + (((verdicts[q.name] || {}).all || 0) > human(q)
              ? ', ' + num(((verdicts[q.name] || {}).all || 0) - human(q)) + ' by an agent' : '')],
          ])),
          section('the answers it can give', opts),
        ],
      });
    },
  }));
}

/* ------------------------------------------------------------------------------- Config */
function configTab(host) {
  /* *** A REPORT YOU SCROLLED THROUGH TO FIND THE PART YOU WANTED. ***
     "instead of it showing vocab then requiring you to scroll down to per check policy then
     waivers ... make it like [Areas]". The sections are the groups on the left, a section's
     entries are the middle list, and the one picked is on the right. */
  const c = DATA.config || {};
  const brief = v => {
    if (v == null) return '';
    if (typeof v !== 'object') return String(v);
    if (Array.isArray(v)) return v.map(brief).join(', ');
    for (const k of ['means', 'action', 'reason', 'what', 'question', 'applies_to'])
      if (v[k] != null && typeof v[k] !== 'object') return String(v[k]);
    const first = Object.values(v).find(x => x != null && typeof x !== 'object');
    return first == null ? Object.keys(v).join(', ') : String(first);
  };
  const entries = obj => Object.keys(obj || {}).sort().map(k => ({name: k, value: obj[k]}));
  const settingCols = (label) => [
    {key: 'name', label: label, mono: 1, val: r => r.name},
    {key: 'v', label: 'value', clip: 1, val: r => brief(r.value)}];
  const groups = [];
  /* The settings and the plate stay on top, as they were; vocabulary and everything after it
     is the navigator underneath. */
  const top = el('div', {class: 'cfgtop'});
  const cut = (DATA.cuts || {}).tower;
  if (cut) top.append(el('img', {class: 'cut tabcut', src: cut, alt: ''}));
  const scalars = Object.entries(c).filter(([, v]) => typeof v !== 'object' || v === null);
  if (scalars.length) top.append(section('resolved', kv(scalars.map(([k, v]) => [k, String(v)])),
    'The values assay is using once its defaults are filled in, which can differ from audit.yml. '
    + 'Everything below this you wrote by hand.'));
  top.append(el('div', {class: 'clearcut'}));
  if (c.vocab && Object.keys(c.vocab).length) groups.push({key: 'vocab', label: 'vocabulary',
    rows: entries(c.vocab), cols: settingCols('term').map(x => x.key === 'v'
      ? {...x, label: 'what it means here'} : x),
    detail: r => pane({kind: 'vocabulary term', title: [wbr(r.name)], mono: 1,
      what: (r.value || {}).means || null,
      reading: [section('as written in audit.yml', kvAny(r.value),
        'Sent with every judged question whose subject it applies to.')]})});
  if (c.questions && Object.keys(c.questions).length) groups.push({key: 'policy',
    label: 'per-check policy', rows: entries(c.questions),
    cols: settingCols('check').map(x => x.key === 'v' ? {...x, label: 'action'} : x),
    detail: r => pane({kind: 'per-check policy', title: [wbr(r.name)], mono: 1,
      reading: [section('as written in audit.yml', kvAny(r.value))]})});
  if (c.waivers && Object.keys(c.waivers).length) {
    const rows = [];
    for (const m of Object.keys(c.waivers).sort())
      for (const w of [].concat(c.waivers[m])) rows.push({name: m, value: w});
    groups.push({key: 'waivers', label: 'waivers', rows: rows,
      cols: [{key: 'name', label: 'model, or a named waiver', mono: 1, val: r => r.name,
              cell: r => link(r.name)},
             {key: 'v', label: 'why', clip: 1, val: r => brief(r.value)}],
      detail: r => pane({kind: 'waiver', title: link(r.name),
        what: (r.value || {}).reason || null,
        reading: [section('as written in audit.yml', kvAny(r.value),
          'A waived finding never reaches the Findings tab. The reason is required, because a '
          + 'waiver that says "looks fine" is how a real finding gets silenced.')]})});
  }
  for (const k of ['practices', 'explanations'])
    if (c[k] && Object.keys(c[k]).length) groups.push({key: k, label: k, rows: entries(c[k]),
      cols: settingCols(k === 'practices' ? 'practice' : 'mart'),
      detail: r => pane({kind: k === 'practices' ? 'practice' : 'row explanations',
        title: [wbr(r.name)], mono: 1, reading: [section('as written in audit.yml', kvAny(r.value))]})});
  if (DATA.runs.length) groups.push({key: 'runs', label: 'runs recorded', rows: DATA.runs,
    cols: [{key: 'when', label: 'started', mono: 1, val: r => r.started_at || ''},
           {key: 'av', label: 'assay', mono: 1, val: r => r.assay_version},
           {key: 'no', label: 'unreadable', n: 1, val: r => r.unreadable,
            cell: r => el('span', {class: r.unreadable ? 'bad' : 'tot', text: num(r.unreadable)})}],
    sort: 'when', dir: -1,
    detail: r => pane({kind: 'run', title: String(r.started_at || r.run_id),
      reading: [section('what it ran on', kv([['run', r.run_id], ['assay', r.assay_version],
        ['dbt', r.dbt_version], ['models', num(r.models)], ['readable', num(r.readable)],
        ['unreadable', num(r.unreadable)]]))]})});
  if (DATA.unreadable.length) groups.push({key: 'unreadable', label: 'models assay could not read',
    rows: DATA.unreadable,
    cols: [{key: 'name', label: 'model', mono: 1, val: u => u.name},
           {key: 'why', label: 'why', clip: 1, val: u => u.why}],
    detail: u => pane({kind: 'could not read', title: [wbr(u.name)], mono: 1,
      where: el('span', {class: 'mono', text: u.path || ''}), what: u.why,
      reading: [el('p', {class: 'prose', text: 'Its SQL would not parse, so it is missing from '
        + 'every other tab and nothing was checked on it.'})]})});
  if (!groups.length) {
    host.replaceChildren(top);
    return;
  }
  const kindOf = r => groups.find(g => g.rows.includes(r));
  host.classList.add('cfgpanel');
  host.replaceChildren(top, drill({
    noun: 'settings', groups: groups, all: false, keepOrder: 1,
    chip: g => g.label, groupFilter: 'find a section...',
    rowsOf: g => g.rows, rowCols: groups[0].cols,
    colsFor: g => g.cols, sortFor: g => g.sort || 'name', dirFor: g => g.dir || 1,
    rowFilter: 'filter...',
    rowText: r => JSON.stringify(r),
    detailOf: r => (kindOf(r) || groups[0]).detail(r),
  }));
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

/* Open findings per run, as a row of thin bars: HTML like the other Overview charts, and each
   run its own mark with its numbers on hover. */
function sparkline(trend) {
  const max = Math.max(...trend.map(t => t.open), 1);
  return el('div', {class: 'spark'}, trend.map(t => el('span', {
    style: 'height:' + Math.max(2, Math.round(100 * t.open / max)) + '%',
    title: t.at + ': ' + num(t.open) + ' open' + (t.harm ? ', ' + num(t.harm) + ' happening now'
                                                            : '')})));
}

function tile(big, label, note, cls) {
  return el('div', {class: 'tile'}, [
    el('div', {class: 'tilebig ' + (cls || ''), text: big}),
    el('div', {class: 'tilelab', text: label}),
    el('div', {class: 'tilenote', text: note || ''}),
  ]);
}

/* *** A GREY SENTENCE UNDER EVERY HEADING IS A SENTENCE NOBODY READS. ***
   "all those are pointless, some contain useful info but theyre all not being read its random
   text at the top ... your eyes dont even notice it". So a section's explanation is its heading's
   tip, and only a FACT -- something that is true of this warehouse, like "2 of 3 monitors have
   stopped" -- is set as text, and it is set as a line of the content, in ink, not as a caption. */
/* *** 26 NAMES IN ONE COMMA RUN. *** (Ryan) What a finding reaches, grouped by the name before
   its colon ("CO commercial: ..."), one line per group with its count, and the names one click
   away. The line above says how many, and how many of them customers pay for. */
function reachGroups(names) {
  const by = {}, order = [];
  for (const n of names) {
    const i = String(n).indexOf(': ');
    const k = i > 0 ? n.slice(0, i) : n;
    if (!by[k]) { by[k] = []; order.push(k); }
    by[k].push(i > 0 ? n.slice(i + 2) : '');
  }
  return order.map(k => ({group: k, items: by[k].filter(Boolean), n: by[k].length}));
}
function reachesBox(names) {
  const meta = DATA.exposure_meta || {};
  const paid = names.filter(n => (meta[n] || {}).customer_facing).length;
  const box = el('div', {class: 'reach'});
  box.append(el('div', {text: num(names.length) + (names.length === 1 ? ' exposure' : ' exposures')
    + (paid ? ', ' + num(paid) + ' customer-facing' : '')}));
  for (const g of reachGroups(names)) {
    if (!g.items.length) { box.append(el('div', {class: 'rg', text: g.group})); continue; }
    box.append(el('details', {class: 'rg'}, [
      el('summary', {text: g.group + ': ' + num(g.n)}),
      el('div', {class: 'rgi', text: g.items.join(' \u00b7 ')})]));
  }
  return box;
}

function block(title, tipText, node, fact) {
  const b = el('div', {class: 'ovblock'});
  b.append(el('h3', {}, [el('span', {text: title, tip: tipText})]));
  if (fact) b.append(el('p', {class: 'fact', text: fact}));
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
      'No rule found a candidate. Each rule needs evidence that `assay check` and `assay probe` '
      + 'record, and waivers need rulings, so a new store has none to propose.'})));
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
      /* *** "RANK 190,032" WAS A SCALE AND A COUNT FOLDED INTO ONE NUMBER. ***
         What the position rests on, in the rule's own words. It still sorts by the number. */
      {key: 'rank', label: 'ordered by', val: r => r.rank,
       cell: r => el('span', {class: 'tot'}, [wbr(r.ordered_by || '')])},
    ],
    rowSort: 'rank', rowDir: -1,
    rowText: r => [r.headline, r.key, (r.measured || []).join(' ')].join(' '),
    detailOf: (r, g) => {
      const bits = [el('h2', {text: r.key || r.headline})];
      bits.push(el('div', {class: 'path mono', text: (LABEL[r.section] || r.section)
                                                     + '  \u00b7  ' + (r.basis || '')}));
      bits.push(el('p', {class: 'prose', text: r.headline}));
      if (r.ordered_by) bits.push(el('p', {class: 'note', text: 'Placed by: ' + r.ordered_by + '.'}));
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
        el('pre', {class: 'sug-y', text: r.draft}),
        /* The rule that was the tab's opening sentence, on the draft it governs. */
        r.section === 'descriptions'
          ? 'Each line says whether it is QUOTED from a sentence this project already uses, or '
            + 'built from facts assay recorded. assay writes no meaning of its own.'
          : 'Every means: and implies: is blank, or QUOTED from a sentence this project already '
            + 'uses, with where it came from. assay never writes a meaning: a plausible guess '
            + 'would ride along with every judged question after it.'));
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

  function column(label, n, unit, sub, tipText) {
    const c = el('div', {class: 'tcol'});
    c.append(el('div', {class: 'tlab'}, [el('span', {text: label, tip: tipText})]));
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
    leadBits.push(num(answered) + ' questions answered across ' + num(meta.yours ?? meta.models) + ' models');
  const leadCost = spent == null ? '' : ' for $' + spent.toFixed(2);
  /* *** 2,202 NOTES ARE NOT 2,202 PROBLEMS. *** (Ryan: "tone it down", and then: "that doesnt
     mean start claiming any warehouse is suddenly perfect") What is wrong, at its real size:
     what is broken now, what is worth a look, and the notes on one line. No verdict either way. */
  const T = meta.review || {};
  const Q = F.filter(f => f.action === 'queue' || f.action === 'fail');
  const paidN = (T.broken_paid || 0) + (T.look_paid || 0);
  const triage = T.queued != null
    ? num(T.broken) + ' broken now and ' + num(T.look) + ' worth a look'
      + (paidN ? ', ' + num(paidN) + ' of them customer-facing' : '')
      + '; ' + num(T.notes) + ' notes.'
    : num(F.length) + ' findings.';
  bits.push(el('p', {class: 'tlead', text: (leadBits.length
    ? leadBits.join(', and ') + leadCost + '. '
    : 'Read ' + num(meta.yours ?? meta.models) + ' models. ') + triage}));

  const ticket = el('div', {class: 'ticket'}, [
    column('read', num(DATA.claims.length), 'sentences',
           (classified === DATA.claims.length ? 'Every one' : num(classified))
           + ' classified by what job it does; ' + num(meta.models)
           + ' models and ' + num(DATA.edges.length) + ' hops parsed.'),
    column('judged', num(answered), 'answers',
           'from ' + num(DATA.questions.length) + ' questions',
           'Questions no parser can settle -- what a filter is for, what a NULL means -- each '
           + 'answer stored with what it was asked from.'),
    column('queued', num(T.queued != null ? T.queued : F.length), 'findings',
           T.queued != null ? num(T.broken) + ' broken now, ' + num(T.look) + ' worth a look'
                            : 'across ' + num(checks) + ' checks',
           'What audit.yml puts in front of a person. The notes are not counted here: they are '
           + 'in Explore, and inside the changes that clear them.'),
    column('at cost', spent == null ? '\u2014' : '$' + spent.toFixed(2), '',
           spent == null ? 'unknown: this store predates the ledger'
             : num((DATA.cost || {}).calls || 0) + ' model call(s)',
           'Every call recorded, one row each. The Spend tab has the ledger.'),
  ]);
  /* The plate earns its place by being the thing the page is named after: a charge going into a
     furnace and something being drawn off it. */
  const cut = (DATA.cuts || {}).condensers;
  bits.push(el('div', {class: 'ticketwrap'}, [
    ticket,
    cut ? el('img', {class: 'cut ticketcut', src: cut, alt: ''}) : el('div'),
  ]));

  // ---- what is open, and what to change next: the counts beside the changes (the Fix section)
  const FX = DATA.fixes || [];
  if (FX.length || T.queued != null) {
    const cleared = FX.reduce((n, f) => n + (f.resolves || 0), 0);
    const toFix = el('button', {class: 'back', text: 'every change, ranked →'});
    toFix.onclick = () => open('fix');
    /* the changes, ranked by findings cleared per decision, full width below the counts; a change
       whose items are each their own decision says so, since that is why it sits where it does */
    /* "clears N" on one line in its own cell; why a change sits lower (each item its own
       decision) is a small line under it, never a longer row */
    const clears = f => el('td', {class: 'cn clears'}, [
      el('div', {text: 'clears ' + num(f.resolves || 0)}),
      ...((f.decisions || 1) > 1 ? [el('div', {class: 'dec', text: num(f.decisions)
        + ' decisions, one per item'})] : [])]);
    const changes = FX.length ? el('div', {class: 'changesbelow'}, [
      el('table', {class: 'clist changes'}, FX.slice(0, 12).map((f, i) => el('tr', {}, [
        el('td', {class: 'cn rank', text: num(i + 1)}),
        el('td', {class: 'ctitle2', text: f.title}),
        clears(f)]))),
      el('p', {class: 'fact', text: num(FX.length) + (FX.length === 1 ? ' change clears '
        : ' changes clear ') + num(cleared) + ' findings.'}),
      toFix]) : null;
    /* *** THE LIST FOR A COUNT OPENS BESIDE IT, IN A PANEL THAT DOES NOT GROW. *** (Ryan: "an
       enormous scrolled render out of nowhere") One count is picked on arrival, so the panel is
       never empty; picking another swaps what the panel holds, and the page never moves. */
    const panel = el('div', {class: 'countpanel'});
    const inner = el('div', {class: 'countinner'});
    panel.append(inner);
    const F_ = DATA.findings;
    const cut = t => el('td', {class: 'one', text: t, title: t});
    const fRows = fs => fs.map(f => el('tr', {}, [cut(f.model || ''), cut(f.title || f.check),
      el('td', {class: 'sum', text: f.summary || ''})]));
    const CAP = 200;
    const listOf = {
      broken: () => fRows(F_.filter(f => f.bucket === 'broken')),
      look: () => fRows(F_.filter(f => f.bucket === 'look')),
      paid: () => fRows(F_.filter(f => f.paid && f.bucket !== 'note')),
      fix: () => FX.map(f => el('tr', {}, [el('td', {class: 'one wide', text: f.title, title: f.title}),
                                           clears(f)])),
      decide: () => fRows(F_.filter(f => f.decided_on === 'decide')),
      note: () => fRows(F_.filter(f => f.bucket === 'note')),
    };
    const paidN = (T.broken_paid || 0) + (T.look_paid || 0);
    const rowsDef = T.queued != null ? [
      ['broken', 'broken now', T.broken], ['look', 'worth a look', T.look],
      ...(paidN ? [['paid', 'customer-facing', paidN]] : []),
      ['fix', FX.length === 1 ? 'change' : 'changes', FX.length],
      ['decide', 'calls to decide', F_.filter(f => f.decided_on === 'decide').length],
      ['note', 'notes', T.notes]] : [];
    const table = el('table', {class: 'counts'});
    function pick(key) {
      table.querySelectorAll('tr').forEach(r => r.classList.toggle('on',
        r.getAttribute('data-k') === key));
      const rows = listOf[key]();
      const t = el('table', {class: 'clist' + (key === 'fix' ? '' : ' fixed')}, rows.slice(0, CAP));
      inner.replaceChildren(...(rows.length ? [t] : [el('p', {class: 'fact', text: 'none'})]),
        ...(rows.length > CAP ? [el('p', {class: 'fact', text: 'and ' + num(rows.length - CAP)
          + ' more, listed under Explore'})] : []));
      inner.scrollTop = 0;
    }
    for (const [key, label, n] of rowsDef) {
      const tr = el('tr', {'data-k': key}, [el('td', {text: label}),
                                            el('td', {class: 'cn', text: num(n || 0)})]);
      tr.onclick = () => pick(key);
      table.append(tr);
    }
    const start = (rowsDef.find(r => r[2]) || rowsDef[0] || [])[0];
    if (start) pick(start);
    if (rowsDef.length) bits.push(block('What to change next', '',
      el('div', {class: 'nextgrid'}, [table, panel])));
    if (changes) bits.push(block('Changes, ranked by what they clear', '', changes));
  }

  // ---- whether it is getting better: open findings over the full runs in the store
  const TR = DATA.trend || [];
  if (TR.length > 1) {
    const last = TR[TR.length - 1], prev = TR[TR.length - 2];
    const d = last.open - prev.open;
    bits.push(block('Is it getting better',
      'Open findings at each full run in the store, oldest first. A fix batch shows as the drop '
      + 'after it.',
      el('div', {}, [el('div', {class: 'tiles'}, [
        tile(num(last.open), 'findings at the last full run', last.at + ', notes included'),
        tile((d > 0 ? '+' : d < 0 ? '\u2212' : '') + num(Math.abs(d)), 'since the run before',
             prev.at, d > 0 ? 'bad' : ''),
        tile(num(last.harm), 'happening now', 'a failing test, a lost guarantee, a broken key, '
             + 'a stopped monitor', last.harm ? 'bad' : ''),
        tile(num(TR.length), 'full runs', 'since ' + TR[0].at),
      ]), sparkline(TR)])));
  }

  // ---- the loop number. It moves only when a person reads SQL, which is why it is not the hero.
  const unread = meta.models - (meta.coverage || {}).readable;
  bits.push(block('How much of it a person has actually read',
    'The one number no release can move. Agent rulings triage what to read first and gate '
    + 'nothing.',
    el('div', {class: 'tiles'}, [
      tile(num(Q.filter(f => f.ruled_finding).length) + ' of ' + num(Q.length),
           'queued findings ruled on',
           humanN + ' human verdict(s), ' + agentN + ' agent', ruledN ? '' : 'bad'),
      tile(num((meta.coverage || {}).readable || 0), 'assay could read',
           unread ? num(unread) + ' could not be read, so were not checked' : 'all of them',
           unread ? 'bad' : ''),
      tile(num(DATA.claims.filter(c => c.contradicted != null).length), 'claims the code contradicts',
           'of ' + num(DATA.claims.length) + ' extracted'),
      tile(num(DATA.questions.length), 'questions', num(meta.sources) + ' sources read'),
    ])));

  // ---- the loop: of what a person agreed was real, how much went, and how much came back
  const LP = DATA.loop || {};
  if (LP.agreed) {
    const back = el('button', {class: 'back', text: 'the ones that came back →'});
    back.onclick = () => { open('findings');
      const f = (DATA.findings || []).find(x => x.check === 'fixed_finding_returned');
      if (f) GO.findings(f.id); };
    bits.push(block('Did fixing them work',
      'Of the findings a person agreed were real: gone after the code changed, still here, and '
      + 'fixed then back again. A release cannot move these, and neither can an agent.',
      el('div', {}, [el('div', {class: 'tiles'}, [
        tile(num(LP.fixed || 0), 'fixed', 'of ' + num(LP.agreed) + ' agreed with'),
        tile(num(LP.still_open || 0), 'still open', 'agreed with and not dealt with',
             LP.still_open ? 'bad' : ''),
        tile(num(LP.regressed || 0), 'regressed', 'fixed, and back again',
             LP.regressed ? 'bad' : ''),
      ]), ...(LP.regressed ? [back] : [])])));
  }

  // ---- what the findings rest on, and whether it still holds
  const PR = (DATA.premises || []).filter(p => !p.package);
  if (PR.length) {
    const notH = PR.filter(p => p.status !== 'holding');
    const brk = PR.filter(p => p.status === 'broken');
    const held = notH.reduce((n, p) => n + (p.uses || []).filter(u => u.kind === 'held_back').length, 0);
    const go = el('button', {class: 'back', text: 'see them in Guarantees →'});
    go.onclick = () => open('guarantees');
    bits.push(block('What the findings rest on',
      'Keys a declared grain or a held-back finding assumes are unique. Broken means a count or '
      + 'a failed test says otherwise; unchecked means nothing has checked it.',
      el('div', {}, [el('div', {class: 'tiles'}, [
        /* B4: broken leads; "not holding" counted not-yet-checked ones as wrong */
        tile(num(brk.length), 'broken', brk.length ? 'a count or a failed test says otherwise; '
             + 'a finding held back on one is raised again' : 'none measured false',
             brk.length ? 'bad' : ''),
        tile(num(notH.length - brk.length), 'not yet checked',
             num(PR.filter(p => p.status === 'unchecked').length) + ' unchecked, '
             + num(PR.filter(p => p.status === 'unknown').length) + ' with no evidence, of '
             + num(PR.length) + ' in all'),
        tile(num(held), 'findings held back', 'on a premise that is not holding'),
        ...(PROOFS.length ? [tile(num(PROOFS.filter(r => r.status === 'proven').length) + ' of '
            + num(PROOFS.length), 'properties proven',
            num(PROOFS.filter(r => r.status === 'proven' && (r.run_check || {}).status
              === 'holds').length) + ' held on a run of the model; '
            + num(PROOFS.filter(r => r.guarantee === 'lost').length) + ' guarantee(s) lost',
            PROOFS.some(r => r.guarantee === 'lost') ? 'bad' : '')] : []),
      ]), go])));
  }

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

  // ---- the queued findings by check, ranked, one hue
  const byCheck = {};
  for (const f of F) byCheck[f.check] = (byCheck[f.check] || 0) + 1;
  const byQ = {};
  for (const f of Q) byQ[f.check] = (byQ[f.check] || 0) + 1;
  const rows = Object.entries(byQ).sort((a, b) => b[1] - a[1]).map(([c, n]) => {
    const mine = Q.filter(f => f.check === c);
    const read = mine.filter(f => f.ruled_finding).length;
    return {label: c, n: n, note: read ? read + ' read' : '',
            tip: `${c}: ${n} finding(s), ${read} read by a person, worst reach `
                 + Math.max(...mine.map(f => f.marts)) + ' marts',
            onclick: () => { open('findings'); }};
  });
  if (rows.length) bits.push(block('What is queued, by check',
    'Ranked by count. The notes are in Explore. Click a bar for the findings.',
    rankedBars(rows)));

  // ---- what would happen on a build. STATUS colors, always with their label.
  const acts = {fail: 0, queue: 0, annotate: 0, waived: (DATA.waived || []).length};
  for (const f of F) if (acts[f.action] != null && f.action !== 'waived') acts[f.action]++;
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
    el('div', {}, [stackedBar(aparts.filter(p => p.n), F.length + acts.waived), alegend])));

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
        tip: `${r.family.replace(/_/g, ' ')}, ruled under ${r.prompt_version || 'no recorded version'}: `
             + `${r.n} ruled, `
             + `${r.agreement == null ? 'no rate' : Math.round(r.agreement * 100) + '% agreed'}, `
             + `${r.unclear} unclear, ${r.open_disagreements} open disagreement(s)`,
      }))));
    }
    bits.push(block('Did the questions get better?',
      'Per question, per version. Unclear is excluded: wrong criteria and a thin state need '
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
    bits.push(block('What moved since the previous run', null, null,
      'Only one run is recorded, so nothing can have moved yet -- which is different from '
      + 'nothing having moved. Run `assay check` again after your next change.'));
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
/* *** A WINDOW IS CALENDAR DAYS, AND A DAY NOTHING RAN IS STILL A DAY. ***
   The chart drew the last 30 days that had a row, so a quiet week vanished and the axis lied
   about time. `calendar` lays the ledger's days on a real calendar ending on the LAST DAY
   RECORDED -- never the viewer's clock, which would make an old ledger look empty and a rerun of
   the same file look different -- and a day with no row is an empty column that says so. */
function calendar(days, n) {
  if (!days.length) return [];
  const by = Object.fromEntries(days.map(d => [d.day, d]));
  const sorted = days.map(d => d.day).sort();
  const last = new Date(sorted[sorted.length - 1] + 'T00:00:00Z');
  const first = n ? new Date(last.getTime() - (n - 1) * 86400000)
                  : new Date(sorted[0] + 'T00:00:00Z');
  const out = [];
  for (let t = first.getTime(); t <= last.getTime(); t += 86400000) {
    const k = new Date(t).toISOString().slice(0, 10);
    out.push(by[k] || {day: k, empty: 1});
  }
  return out;
}

function daySeries(days, pick, fmt) {
  const max = Math.max(...days.map(d => pick(d) || 0), 0);
  /* Past a month the columns get narrow, so only some carry a date: every 7th, and the last. */
  const every = days.length > 45 ? 7 : 1;
  const host = el('div', {class: 'days' + (days.length > 45 ? ' dense' : '')});
  days.forEach((d, i) => {
    const v = d.empty ? 0 : (pick(d) || 0);
    const col = el('div', {class: 'day' + (v ? '' : ' zero'),
                           title: d.day + ' · ' + (d.empty ? 'nothing recorded' : fmt(d))
                                  + (d.why ? '\n' + d.why : '')});
    const track = el('div', {class: 'daytrack'});
    track.append(el('div', {class: 'dayfill',
                            style: `height:${max ? Math.max(d.empty ? 0 : 2, (v / max) * 100) : 2}%`}));
    col.append(track);
    const lab = (days.length - 1 - i) % every === 0 ? d.day.slice(5) : '';
    col.append(el('div', {class: 'daylab', text: lab}));
    host.append(col);
  });
  return host;
}

function spendTab(host) {
  const c = DATA.cost || {};
  const bits = [];
  if (!c.calls && !((c.warehouse || {}).calls)) {
    /* An absent ledger is not a free project. This store predates `model_calls`, or nothing has
       been asked here -- two different facts, and neither of them is a zero. */
    bits.push(block('No ledger in this store', null, null,
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
  bits.push(block('What this project has cost', 'What the thinking cost and what the warehouse '
    + 'cost, kept apart because they are priced by different people in different units.',
    el('div', {class: 'tiles'}, [
    tile(money(c.usd || 0), 'thinking', num(c.calls || 0) + ' model call(s)'),
    tile(wh.calls ? money(wh.usd) : '--', 'the warehouse',
         wh.calls ? num(wh.calls) + ' statement(s), ' + bytes(wh.bytes_estimated)
                  : 'no statement recorded'),
    tile(num(c.input_tokens || 0), 'input tokens', 'output is shown and never priced'),
  ])));

  const days = c.days || [];
  if (days.length) {
    /* *** "MAYBE ENSURE IT CAN HAVE A CONFIGURABLE WINDOW". ***
       The per-day charts were fixed at the last 30 days that had a row. The window is a choice
       now, remembered for this viewer, and the charts, the window's total and the day table all
       follow it. The by-caller and by-family tables below are the whole ledger and say so. */
    const WINDOWS = [[7, '7 days'], [30, '30 days'], [90, '90 days'], [0, 'everything']];
    let win = 30;
    try { const v = localStorage.getItem('assay.spend.window'); if (v != null) win = Number(v); }
    catch (e) { /* no storage: the default stands */ }
    if (!WINDOWS.some(w => w[0] === win)) win = 30;
    const box = el('div');
    const pickers = el('div', {class: 'gsorts'});
    const paint = () => {
      pickers.replaceChildren(el('span', {class: 'count', text: 'show'}), ...WINDOWS.map(([n, label]) => {
        const b = el('button', {class: 'gsort' + (n === win ? ' on' : ''), text: label});
        b.onclick = () => { win = n;
          try { localStorage.setItem('assay.spend.window', String(n)); } catch (e) { /* none */ }
          paint(); };
        return b;
      }));
      const cal = calendar(days, win);
      const got = cal.filter(d => !d.empty);
      const usd = got.reduce((a, d) => a + (d.usd || 0), 0);
      const calls = got.reduce((a, d) => a + (d.calls || 0), 0);
      const ran = got.filter(d => d.runs).length;
      const spice = el('div');
      spice.append(el('p', {class: 'note', text: cal.length + ' day(s), ' + cal[0].day + ' to '
        + cal[cal.length - 1].day + ', ending on the last day anything was recorded. In them: '
        + money(usd) + ' over ' + num(calls) + ' model call(s), and something ran on ' + ran
        + ' of the ' + cal.length + ' day(s).'}));
      spice.append(el('p', {class: 'srclab', text: 'model spend per day'}));
      spice.append(daySeries(cal, d => d.usd, d => money(d.usd) + ' · '
                                                  + num(d.calls) + ' call(s)'));
      if (wh.calls) {
        spice.append(el('p', {class: 'srclab', text: 'warehouse statements per day'}));
        spice.append(daySeries(cal, d => d.warehouse_calls,
                               d => num(d.warehouse_calls) + ' statement(s), '
                                    + bytes(d.warehouse_bytes)));
      }
      spice.append(grid(got, [
        {key: 'day', label: 'day', mono: 1, val: d => d.day},
        {key: 'runs', label: 'runs', n: 1, val: d => d.runs},
        {key: 'calls', label: 'calls', n: 1, val: d => d.calls},
        {key: 'usd', label: 'usd', n: 1, val: d => d.usd,
         cell: d => el('span', {text: money(d.usd)})},
        {key: 'wh', label: 'statements', n: 1, val: d => d.warehouse_calls},
        {key: 'why', label: 'why', val: d => d.why},
      ], {sort: 'day', dir: -1}));
      box.replaceChildren(pickers, spice);
    };
    paint();
    bits.push(block('Per day', 'A day that ran and spent nothing shows zero with the reason '
      + 'beside it. A day nothing ran is an empty column.', box));
  }

  const cols = [
    {key: 'k', label: '', mono: 1, val: r => r[0]},
    {key: 'calls', label: 'calls', n: 1, val: r => r[1]},
    {key: 'tok', label: 'input tokens', n: 1, val: r => r[2]},
    {key: 'usd', label: 'usd', n: 1, val: r => r[3], cell: r => el('span', {text: money(r[3])})},
  ];
  for (const [title, rows] of [['by caller, the whole ledger', c.by_caller],
                             ['by question, the whole ledger', c.by_family]]) {
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
      + 'total, and are not estimated.');
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
  const m = DATA.monitoring || {};
  const cad = m.cadence || {}, cov = m.test_coverage || {};
  const readings = m.readings || [], unwatched = m.unwatched || [], mf = m.monitoring || [];
  const stale = m.stale_failures || [];

  /* *** AN EMPTY TAB READS AS "NOTHING IS WRONG". *** It is not measured until somebody
     measures it, and this says exactly which command does that. */
  if (!cad.runs && !readings.length && !cov.declared) {
    host.replaceChildren(
      block('Nothing here has been measured', 'Taking the measurement needs your dbt connection, '
        + 'and assay never holds a credential, so the page carries it only when handed the file.',
        el('pre', {text: 'assay volume --json > volume.json\n'
                         + 'assay page assay.html --monitoring volume.json'}),
        'The monitoring numbers were not taken for this page.'));
    return;
  }

  /* *** SIX SECTIONS STACKED ON ONE SCROLL, AND THE LONGEST ONE CUT AT 400. ***
     "the others definitely need some love to support the higher volume of shit that needs to be
     able to be viewed and discovered". The sections are the groups of the navigator the other
     high-volume tabs use: every list is paged, each row opens in the pane on the right, and the
     sentences that sat under each heading are the rows' own detail or a column's tip. The facts
     that were paragraphs -- how often this builds, what late means, what the tests are doing --
     are the rows of the first group, so they are read the same way as everything else. */
  const late = cad.derived_staleness_days;
  const glance = [];
  if (cad.explain || cad.runs != null)
    glance.push({what: 'how often this project builds', value: cad.gap_text
                   ? 'every ' + cad.gap_text : (cad.runs ? num(cad.runs) + ' writes' : 'unread'),
                 why: (cad.explain || 'the build cadence could not be read')
                   + '. Every threshold on this tab is derived from it.'
                   + (cad.unreadable ? ' The statement that reads it did not run: ' + (cad.why || '') : '')});
  if (late != null)
    glance.push({what: 'a monitor is called late after', value: late + ' day(s)',
                 why: cad.configured ? 'audit.yml sets this deliberately, overriding every '
                   + 'derived threshold.'
                   : 'Derived from the build cadence' + (cad.floored ? ', held at the one-day '
                   + 'floor because a threshold cannot be shorter than a day' : '')
                   + '. audit.yml does not set it, so this derived number is used. Each monitor '
                   + 'also has its own threshold from its own write history where it has enough.'});
  if (cov.declared) {
    const never = (cov.declared || 0) - (cov.ever_ran || 0);
    const bar = () => {
      const parts = [
        {label: 'have produced a result', n: cov.ever_ran || 0, color: RAMP.declared},
        {label: 'declared, never run', n: never, color: RAMP.judged}].filter(x => x.n);
      return el('div', {}, [stackedBar(parts, cov.declared), el('div', {class: 'legend'},
        parts.map(x => el('span', {class: 'lgi'}, [el('span', {class: 'sw',
          style: 'background:' + x.color}), el('span', {text: x.label + ' ' + num(x.n)})])))]);
    };
    const why = 'Declared is not run, and skipped is not passed: a test that never ran and a '
      + 'test that passed look identical in a summary, and only one of them has read your data.';
    glance.push({what: 'tests declared', value: num(cov.declared), why: why, bar: bar});
    glance.push({what: 'have produced a result', value: num(cov.ever_ran || 0), why: why, bar: bar});
    glance.push({what: 'declared, never run', value: num(never), bad: never > 0, why: why, bar: bar});
    /* Tests and results are different units: a test skipped on 40 runs is 40 results. The
       count in TESTS is shown when `volume` measured it; otherwise the result count, named as
       what it is, so it is never read against the number of tests declared. */
    if (cov.skipped_now != null)
      glance.push({what: 'tests skipped as of their last run', value: num(cov.skipped_now),
                   bad: cov.skipped_now > 0, bar: bar,
                   why: 'Tests whose most recent result was SKIPPED: dbt skips a test when the '
                     + 'model under it failed, so these have not read your data since. Across '
                     + 'every run there are ' + num(cov.skipped_results || 0) + ' skipped results.'});
    else
      glance.push({what: 'skipped results, across every run', value: num(cov.skipped_results || 0),
                   bad: (cov.skipped_results || 0) > 0, bar: bar,
                   why: 'Each run that skipped a test adds one, so this counts results and can be '
                     + 'larger than the ' + num(cov.declared) + ' tests declared. Run '
                     + '`assay volume --json` again to count the tests that are skipped now.'});
  }

  /* A declared test that has not run, backing a premise: the key it declares is unchecked. */
  const unrunBacking = (DATA.premises || []).filter(p => p.status === 'unchecked' && !p.package
    && (p.evidence || []).some(e => e.kind === 'declared'));
  if (unrunBacking.length)
    glance.push({what: 'unrun tests that a grain or a held-back finding rests on',
                 value: num(unrunBacking.length), bad: true,
                 why: 'Each declares a key unique, and something here leans on that key. Until '
                   + 'the test runs the key is unchecked: the Guarantees tab lists them.'});
  const groups = [
    {key: 'monitors', label: 'the monitors themselves', rows: readings,
     cols: [
       {key: 'relation', label: 'relation', mono: 1, val: r => r.relation},
       {key: 'state', label: 'state', val: r => r.state,
        tip: 'live: written to recently. abandoned: nothing has written to it for longer than its '
          + 'threshold, and a monitor that stopped reads exactly like one that finds nothing.',
        cell: r => el('span', {class: r.state === 'live' ? '' : 'bad',
                               text: String(r.state).replace(/_/g, ' ')})},
       {key: 'age', label: 'days since', n: 1, val: r => r.age_days,
        tip: 'Days since anything was written to it.',
        cell: r => el('span', {text: r.age_days == null ? '' : r.age_days.toFixed(1)})}],
     sort: 'relation',
     detail: r => {
       const pr = (cad.per_relation || {})[r.relation] || {};
       return [el('h2', {class: 'mono', text: r.relation}),
         kv([['state', el('span', {class: r.state === 'live' ? '' : 'bad',
                                   text: String(r.state).replace(/_/g, ' ')})],
             ['rows', num(r.rows)], ['newest write', r.newest || '—'],
             ['days since', r.age_days == null ? '—' : r.age_days.toFixed(1)],
             ['late after', pr.threshold_days != null ? pr.threshold_days + ' day(s)' : '—']]),
         ...(r.says ? [section('what it means', md(r.says))] : []),
         ...(pr.explain ? [section('where the threshold came from', md(pr.explain))] : [])];
     }},
    {key: 'stale', label: 'last result a failure, not run since', rows: stale,
     cols: [
       {key: 'table', label: 'table', mono: 1, val: r => r.table, cell: r => link(r.table)},
       {key: 'kind', label: 'what failed', val: r => r.kind + (r.sub_type ? ' · ' + r.sub_type : '')},
       {key: 'age', label: 'days since', n: 1, val: r => r.age_days,
        cell: r => el('span', {text: r.age_days == null ? '' : r.age_days.toFixed(0)})}],
     sort: 'age', dir: -1,
     detail: r => [el('h2', {}, [link(r.table)]),
       kv([['what failed', r.kind + (r.sub_type ? ' · ' + r.sub_type : '')],
           ['days since it last ran', r.age_days == null ? '—' : r.age_days.toFixed(0)]]),
       el('p', {class: 'prose', text: 'Its last result was a FAILURE and it has not run since, so the failure is out of date. '
          + 'Views that sort by status still show it as failing.'})]},
    {key: 'findings', label: 'findings about the monitoring', rows: mf,
     cols: [
       {key: 'check', label: 'check', mono: 1, val: f => f.check},
       {key: 'summary', label: 'what', clip: 1, val: f => f.summary}],
     sort: 'check',
     detail: f => [el('h2', {text: f.check.replace(/_/g, ' ')}), md(f.summary || ''),
       section('evidence', kvAny(f.evidence)),
       el('p', {class: 'prose', text: 'A statement about whether something is watching, never '
         + 'about your data.'})]},
    {key: 'unwatched', label: 'models nothing watches', rows: unwatched,
     cols: [
       {key: 'model', label: 'model', mono: 1, val: r => r.model, cell: r => link(r.model)},
       {key: 'marts', label: 'marts', n: 1, val: r => r.marts,
        tip: 'Marts downstream. A mart downstream is what makes an unnoticed change expensive.'},
       {key: 'descendants', label: 'descendants', n: 1, val: r => r.descendants}],
     sort: 'marts', dir: -1,
     detail: r => [el('h2', {}, [link(r.model)]),
       kv([['marts downstream', num(r.marts)], ['descendants', num(r.descendants)]]),
       el('p', {class: 'prose', text: 'No volume history covers this model. assay does not '
         + 'measure volume and does not intend to; elementary-data does, and it is a dbt '
         + 'package.'})]},
  ].filter(g => g.rows.length);
  const kindOf = r => groups.find(g => g.rows.includes(r));

  /* *** AT A GLANCE IS ALWAYS ON TOP. *** The facts about the whole warehouse's monitoring --
     the monitors against their thresholds, how often it builds, what the tests are doing -- are
     one strip above the navigator, and the navigator holds only the lists. */
  function glanceTop() {
    const box = el('div', {class: 'mtop'});
    const chart = monitorsChart();
    const facts = el('div', {class: 'mfacts'}, glance.map(r => el('div', {class: 'mfact'}, [
      el('div', {class: 'mfv' + (r.bad ? ' bad' : ''), text: r.value}),
      el('div', {class: 'mfl'}, [el('span', {text: r.what, tip: r.why})])])));
    if (chart) box.append(chart);
    box.append(facts);
    const cov = glance.find(r => r.bar);
    if (cov) box.append(cov.bar());
    return box;
  }

  /* *** THE TAB OPENED ON TEXT AND A TABLE. *** (P11) The picture that answers "is anything
     watching" first: each monitor's days since its last write against the threshold that makes
     it late, so a stopped monitor is a long bar past its mark. */
  function monitorsChart() {
    if (!readings.length) return null;
    const pr = cad.per_relation || {};
    const rows = readings.map(r => ({r: r, age: r.age_days, th: (pr[r.relation] || {}).threshold_days}));
    const max = Math.max(...rows.map(x => Math.max(x.age || 0, x.th || 0)), 1);
    const box = el('div', {class: 'mchart'});
    box.append(el('div', {class: 'tlab'}, [el('span', {text: 'days since each monitor last wrote',
      tip: 'The bar is days since the monitor last wrote a row. The mark is the point after which '
        + 'it counts as stopped, derived from its own write history.'})]));
    for (const x of rows) {
      const late = x.th != null && x.age != null && x.age > x.th;
      const track = el('span', {class: 'mtrack'});
      track.append(el('span', {class: 'mfill' + (late ? ' late' : ''),
        style: 'width:' + Math.max(1, (x.age || 0) / max * 100) + '%'}));
      if (x.th != null) track.append(el('span', {class: 'mmark', style: 'left:' + (x.th / max * 100) + '%',
        title: 'late after ' + x.th + ' day(s)'}));
      box.append(el('div', {class: 'mrow'}, [
        el('span', {class: 'mono mlab'}, [wbr(x.r.relation)]), track,
        el('span', {class: 'mval' + (late ? ' bad' : ''), text: x.age == null ? 'never'
          : x.age.toFixed(1) + ' days' + (x.th != null ? ', late after ' + x.th : '')})]));
    }
    const marts = unwatched.filter(u => u.marts).length;
    if (unwatched.length) box.append(el('p', {class: 'fact', text: num(marts) + ' model(s) with a '
      + 'mart downstream have nothing watching their volume.'}));
    return box;
  }

  host.classList.add('cfgpanel');
  host.replaceChildren(glanceTop(), drill({
    noun: 'rows', groups: groups, all: false, keepOrder: 1,
    chip: g => g.label, groupFilter: 'find a section...',
    all: false,
    groupSub: g => g.key === 'monitors'
      ? (() => { const off = g.rows.filter(r => r.state !== 'live').length;
                 return off ? num(off) + ' stopped' : 'all live'; })()
      : g.key === 'unwatched' ? 'each has a mart downstream' : null,
    rowsOf: g => g.rows, rowCols: groups[0].cols,
    colsFor: g => g.cols, sortFor: g => g.sort, dirFor: g => g.dir,
    rowFilter: 'filter...',
    rowText: r => JSON.stringify(r),
    detailOf: r => (kindOf(r) || groups[0]).detail(r),
  }));
}

/* ---------------------------------------------------------------------------- Guarantees */
/* *** WHAT THE FINDINGS REST ON, AND WHETHER IT IS STILL TRUE. ***
   A grain "declared by a test", a hop held back because its parent's key is unique, a pick
   excused by a unique tie-break: each is a statement about the data that something here leans
   on. This tab is every one of them, what measured it and when, and what rests on it. */
const PSTATUS = ['broken', 'unchecked', 'assumed', 'unknown', 'holding'];
let showPackagedPrem = false;
const PCOLOR = {broken: '#a8491a', unchecked: '#cec5b6', assumed: '#d2833a', unknown: '#e4ddd0',
                holding: '#1a1714'};
const PWORD = {broken: 'broken', unchecked: 'unchecked', assumed: 'assumed only',
               unknown: 'no evidence', holding: 'holding'};
const PTIP = {
  broken: 'A count found duplicates, or its test failed on its last run. One is enough.',
  unchecked: 'Declared, and nothing has checked it: the test never ran, was skipped, or no test '
    + 'results were read.',
  assumed: 'Only a judgment carries it: nothing declares it and nothing has counted it.',
  unknown: 'Nothing declares, counts or judges it.',
  holding: 'Its test ran and passed, or an exact count found no duplicates.'};
const USEWORD = {grain: 'the grain of', held_back: 'a finding held back on',
                 raised_on: 'a finding reading it on', proof: 'the proof about'};

/* ---- certificates (L2): a guarantee's word, badge class and tip. */
const GWORD = {holding: 'proven', conditional: 'proven, conditional', lost: 'guarantee lost',
               refuted: 'does not hold', regrouped: 'fans out, regrouped',
               contradicted: 'contradicted by a run',
               stale: 'file changed', not_proven: 'not proven', not_attempted: 'not proven'};
const GCLASS = {holding: 'proven', conditional: 'unchecked', lost: 'broken', refuted: 'assumed',
                regrouped: 'unknown', contradicted: 'broken', stale: 'unchecked',
                not_proven: 'assumed', not_attempted: 'unknown'};
const GTIP = {
  holding: 'Checked by Lean, and every premise it rests on is holding.',
  conditional: 'Checked by Lean. It holds for every input its premises allow, and one of them '
    + 'has not been checked.',
  lost: 'Checked by Lean, and a premise it rests on broke: the guarantee no longer applies.',
  refuted: 'The key it would need was never declared, and a count shows it repeats: this join '
    + 'can multiply this model’s rows, and nothing groups them back.',
  regrouped: 'The join multiplies rows, and the model’s own group by collapses them to its grain '
    + '(proven). Usually deliberate, a join to many readings then grouped; a sum over the joined '
    + 'rows would still count each several times.',
  stale: 'The model’s file changed since this was proven. `assay prove` proves it again.',
  contradicted: 'Lean proved the theorem assay wrote, but a run of the model on inputs meeting '
    + 'its premises did not do what it says: assay read the model wrong. Not a guarantee.',
  not_proven: 'Lean could not close the goal. What is missing is shown.',
  not_attempted: 'No proven rule applies to this structure yet, or a premise is missing.'};
const PROOFS = DATA.proofs || [];
/* Lean proved this certificate (whatever its premises or a run say now): it has premises to show,
   not a missing piece. A refutation is Lean-checked too, and shows what is missing. */
function provedIt(r) { return !['not_proven', 'not_attempted'].includes(r.status); }
/* what needs attention first */
const PROOF_ORDER = {contradicted: 0, lost: 1, refuted: 2, regrouped: 3, stale: 4, conditional: 5,
                     holding: 6, not_proven: 7, not_attempted: 8};
/* A certificate's statement where the model is already named (its own pane, a row beside its
   name): "`m`'s rows" reads "its rows", "`m` is one row per" reads "One row per". */
function ownStatement(r) {
  const n = '`' + r.model_name + '`';
  let s = String(r.statement || '').split(n + '\u2019s').join('its').split(n + "'s").join('its');
  if (s.startsWith(n + ' is one row')) s = 'One row' + s.slice(n.length + ' is one row'.length);
  else if (s.startsWith(n + ' stays ')) s = 'Stays' + s.slice(n.length + 6);
  return s;
}
/* the line under a certificate: what it rests on, once */
function proofSub(r) {
  if (!provedIt(r)) return r.missing || 'no proven rule covers it';
  const ps = r.premises || [];
  if (r.lost_because) return (r.guarantee === 'lost' ? 'lost: ' : 'rests on ')
    + r.lost_because.replace(/`/g, '').replace(/ (broke|is not so): /, ', which $1: ');
  if (!ps.length) return 'needs no premise';
  return 'as long as ' + ps.map(p => String(p.statement || '').replace(/`/g, '') + ' ('
    + p.label + ')').join('; ');
}
const PROOFS_BY_MODEL = {};
for (const r of PROOFS) (PROOFS_BY_MODEL[r.model] = PROOFS_BY_MODEL[r.model] || []).push(r);
function gbadge(r) {
  const b = badge(GWORD[r.guarantee] || r.guarantee, GCLASS[r.guarantee] || 'unknown');
  b.setAttribute('data-tip', GTIP[r.guarantee] || '');
  return b;
}
function proofBlock(r) {
  const prem = el('div', {class: 'ulist'}, (r.premises || []).map(p => {
    const a = el('a', {class: 'lk', href: '#guarantees'}, [wbr(p.statement || p.id)]);
    a.onclick = ev => { ev.preventDefault(); open('guarantees'); GO.guarantees(p.id); };
    return el('div', {class: 'urow'}, [a, premBadge(p.status, p.label, p.why)]);
  }));
  const rows = [
    ['guarantee', gbadge(r)],
    ...(r.lost_because ? [['lost because', wbr(r.lost_because)]] : []),
    ...(provedIt(r) ? [['as long as', (r.premises || []).length ? prem
        : el('span', {class: 'tot', text: 'nothing: it needs no premise'})]] : []),
    ...(!provedIt(r) ? [['what is missing', wbr(r.missing || r.detail || '')]] : []),
    ['the rule', el('span', {}, [el('span', {class: 'mono', text: r.rule || ''}),
      el('span', {class: 'tot', text: '  proven once in assay’s Lean library'})])],
    ['the parse', r.parse ? premBadge(r.parse.status, r.parse.label, r.parse.why) : null],
    /* Lean checks the proof; this checks the statement: the claim run against the model. */
    ['on a run', r.run_check && r.run_check.status !== 'not_run'
      ? premBadge({holds: 'holding', contradicted: 'broken'}[r.run_check.status] || 'unchecked',
          {holds: 'held', contradicted: 'contradicted'}[r.run_check.status] || 'could not run',
          r.run_check.detail) : null],
    /* L4: whether this project's engine does what the rule's constructs mean. */
    ['the engine', (r.engine || []).length && new Set(r.engine.map(x => x.status)).size === 1
        && r.engine[0].status !== 'differs'
      ? el('span', {}, [premBadge({conforms: 'holding'}[r.engine[0].status] || 'unchecked',
          r.engine[0].status === 'conforms' ? 'conforms' : 'not measured',
          r.engine.map(x => x.construct + ': ' + x.detail).join('\n')),
          el('span', {class: 'tot', text: '  ' + r.engine.map(x => x.construct).join(', ')})])
      : (r.engine || []).length ? el('div', {class: 'ulist'}, r.engine.map(x =>
        el('div', {class: 'urow'}, [el('span', {class: 'mono', text: x.construct}),
          /* the badge colour is a premise's: conforms reads as holding, differs as assumed
             (amber), never broken: it is the engine's defined behaviour. (L5) */
          premBadge({conforms: 'holding', differs: 'assumed'}[x.status] || 'unchecked',
            x.status === 'conforms' ? 'conforms' : x.status === 'differs' ? 'differs'
            : 'not measured', x.detail)])))
      : null],
    ['checked', el('span', {class: 'tot', text: (r.lean_version ? 'Lean ' + r.lean_version + ', ' : '')
      + (r.proved_at || '').slice(0, 10) + (r.written_by === 'agent' ? ', written by an agent' : '')})],
  ];
  return kv(rows);
}

function premiseWhatToDo(p) {
  const test = (p.evidence || []).find(e => e.kind === 'declared');
  const obs = (p.evidence || []).find(e => e.kind === 'observed');
  const name = p.name, cols = (p.columns || []).join(', ');
  if (p.status === 'broken' && obs && obs.status === 'broken')
    return 'Remove the duplicates upstream of `' + name + '`, or change what reads it (listed '
      + 'above) to use a key that is unique.';
  if (p.status === 'broken' && test)
    return test.detail.split(' last result')[0] + ' failed on its last run. Fix the data or '
      + 'the key, then run it again: `dbt test --select ' + name + '`.';
  if (p.status === 'unchecked' && test && /no test results were read/.test(test.detail))
    return 'No test results were read. `assay volume` reads each test’s last result from '
      + 'Elementary; a `dbt build` leaves them in target/ for `assay check`.';
  if (p.status === 'unchecked' && test)
    return 'Run it in the next build: `dbt test --select ' + name + '`.';
  if (p.status === 'unchecked')
    return 'Nothing tests it. Declare a `unique` test on ' + cols + ' in `' + name
      + '`, or count it: `assay probe --select ' + name + '`.';
  if (p.status === 'assumed' || p.status === 'unknown')
    return 'Count it: `assay probe --select ' + name + '`, or declare a `unique` test on '
      + cols + '.';
  return null;
}

function guaranteesTab(host) {
  /* Installed packages' premises (Elementary's own tables) are left out unless asked for: nothing
     in this project can fix them. (L3) */
  const PKG_PREM = (DATA.premises || []).filter(p => p.package).length;
  const rows = (DATA.premises || []).filter(p => showPackagedPrem || !p.package);
  if (!rows.length) {
    host.replaceChildren(block('Nothing rests on a premise yet',
      'A premise is recorded when a grain is declared by a test or a check holds a finding back '
      + 'because a key is unique. `assay check` records them.', null,
      'This page was built without a premise ledger.'));
    return;
  }
  const by = {};
  for (const p of rows) (by[p.status] = by[p.status] || []).push(p);
  const moves = DATA.premise_moves || [];
  const broke = moves.filter(m => m.after === 'broken').length;
  const heldOn = ps => ps.reduce((n, p) => n + (p.uses || []).filter(u => u.kind === 'held_back').length, 0);
  const grainsOn = ps => ps.reduce((n, p) => n + (p.uses || []).filter(u => u.kind === 'grain').length, 0);


  const groups = PSTATUS.filter(s => by[s]).map(s => ({key: s, label: PWORD[s], rows: by[s]}));
  /* Certificates, grouped by what their guarantee is now. */
  const pby = {};
  for (const r of PROOFS) {
    const k = r.guarantee === 'not_attempted' ? 'not_proven' : r.guarantee;
    (pby[k] = pby[k] || []).push(r);
  }
  for (const k of ['contradicted', 'lost', 'refuted', 'regrouped', 'conditional', 'stale',
                   'holding', 'not_proven'])
    if (pby[k]) groups.push({key: 'proof_' + k, proof: 1, label: {
      contradicted: 'proofs: contradicted by a run', lost: 'proofs: guarantee lost',
      refuted: 'proofs: does not hold', regrouped: 'proofs: fan out, regrouped',
      conditional: 'proofs: proven, conditional', stale: 'proofs: file changed',
      holding: 'proofs: proven', not_proven: 'proofs: not proven'}[k], rows: pby[k]});
  const proofCols = [
    {key: 'model', label: 'model', mono: 1, val: r => r.model_name, cell: r => link(r.model_name)},
    {key: 'what', label: 'property', val: r => ownStatement(r),
     cell: r => el('span', {}, [wbr(ownStatement(r))])},
    {key: 'g', label: 'guarantee', val: r => r.guarantee, cell: r => gbadge(r)}];
  const proofPane = r => pane({kind: 'proof · ' + String(r.property).split(':')[0].replace(/_/g, ' '),
    title: link(r.model_name), what: ownStatement(r), reading: [proofBlock(r)],
    act: r.guarantee === 'lost' ? el('p', {class: 'prose'}, [wbr('The premise broke: fix the data or '
          + 'the key it names, then `assay check`. Lean need not run again.')])
      : !provedIt(r) ? el('p', {class: 'prose'}, [wbr(r.missing || 'Nothing to do until '
          + 'a proven rule covers this structure.')]) : null});
  const cols = [
    {key: 'statement', label: 'premise', val: p => p.statement,
     cell: p => el('span', {}, [wbr(p.statement)])},
    {key: 'status', label: 'status', val: p => p.status,
     tip: 'What the strongest evidence says. Hover a badge for the evidence.',
     cell: p => premBadge(p.status, p.label, p.why)},
    {key: 'rests', label: 'uses', n: 1, val: p => (p.uses || []).length,
     tip: 'How many grains, held-back findings and proofs rest on it. Since when it has had '
       + 'its status is in the pane.'},
  ];

  function evidenceList(p) {
    const ev = p.evidence || [];
    if (!ev.length) return el('span', {class: 'tot', text: 'none: nothing declares, counts or '
      + 'judges it'});
    const d = el('dl', {class: 'kv'});
    const KIND = {declared: 'a dbt test', config: 'dbt config', observed: 'a count',
                  judged: 'a judgment'};
    for (const e of ev) {
      d.append(el('dt', {text: KIND[e.kind] || e.kind}));
      /* the header already says the deciding piece; its row keeps only where and when */
      d.append(el('dd', {}, [...(e.detail && e.detail !== p.why ? [wbr(e.detail)] : []),
        premBadge(e.status, e.status),
        ...(e.at ? [el('span', {class: 'tot', text: '  ' + String(e.at).slice(0, 10)})] : [])]));
    }
    return d;
  }

  function usesList(p) {
    const us = p.uses || [];
    if (!us.length) return el('span', {class: 'tot', text: 'nothing'});
    const raised = new Set(p.raised || []);
    const d = el('div', {class: 'ulist'});
    for (const u of us) {
      const line = el('div', {class: 'urow'});
      const check = ['grain', 'proof'].includes(u.kind) ? '' : u.dependent.split(':')[0];
      line.append(el('span', {text: (USEWORD[u.kind] || u.kind) + ' '}), link(u.model_name));
      if (check) line.append(el('span', {class: 'mono tot', text: '  ' + check}));
      if (u.detail && u.kind !== 'grain') line.append(el('div', {class: 'usub'}, [wbr(
        u.kind === 'proof' ? ownStatement({statement: u.detail, model_name: u.model_name})
                           : u.detail)]));
      const fid = u.finding || (u.kind === 'held_back' ? (p.raised || []).find(id =>
        (DATA.findings || []).some(f => f.id === id && f.subject === u.model)) : null);
      if (fid) {
        const a = el('a', {class: 'lk plink', href: '#findings',
          text: raised.has(fid) ? 'raised again: the finding' : 'the finding'});
        a.onclick = ev => { ev.preventDefault(); open('findings'); GO.findings(fid); };
        line.append(a);
      }
      d.append(line);
    }
    return d;
  }

  function detailOf(p) {
    const todo = premiseWhatToDo(p);
    return pane({
      kind: 'premise · ' + String(p.property).replace(/_/g, ' '),
      title: el('span', {}, [wbr(String(p.statement).replace(/`/g, ''))]),
      where: link(p.name),
      what: el('span', {}, [premBadge(p.status, p.label, p.why), el('span', {text: ' '}),
                            wbr(p.why || '')]),
      reading: [
        section('evidence', evidenceList(p), 'Every piece of evidence assay has for it, each '
          + 'with what it alone says. A count or a failed test that says false is enough.'),
        section('what rests on it', usesList(p), 'What assay would say differently if this '
          + 'stopped being true.'),
        p.since ? kv([['this status since', p.since]]) : null,
      ],
      act: todo ? el('p', {class: 'prose'}, [wbr(todo)]) : null,
    });
  }

  host.classList.add('cfgpanel');
  const d = drill({
    noun: 'premises', groups: groups, all: false, keepOrder: 1,
    chip: g => g.label, groupFilter: 'find a status...',
    groupSub: g => {
      const h = heldOn(g.rows), gr = grainsOn(g.rows);
      return [h ? num(h) + ' finding(s) held back' : '', gr ? num(gr) + ' grain(s)' : '']
        .filter(Boolean).join(' · ') || null;
    },
    rowsOf: g => g.rows, rowCols: cols,
    colsFor: g => g.proof ? proofCols : cols,
    sortFor: g => g.proof ? 'model' : 'rests', dirFor: g => g.proof ? 1 : -1,
    rowSort: 'rests', rowDir: -1,
    rowFilter: 'filter by model or column...',
    rowText: p => p.theorem ? (p.model_name + ' ' + p.statement)
      : p.statement + ' ' + p.name + ' ' + (p.uses || []).map(u => u.model_name).join(' '),
    detailOf: p => p.theorem ? proofPane(p) : detailOf(p),
  });
  const pkgBox = PKG_PREM ? (() => {
    const cb = el('input', {type: 'checkbox'});
    cb.checked = showPackagedPrem;
    cb.onchange = () => { showPackagedPrem = cb.checked; guaranteesTab(host); };
    return el('label', {class: 'chk'}, [cb, el('span', {text: 'include ' + PKG_PREM
      + ' premise(s) about installed packages’ models'})]);
  })() : null;
  /* Ryan: the list, not a strip of numbers over it; every number it had is a group's count */
  host.replaceChildren(...[pkgBox, d].filter(Boolean));
  GO.guarantees = id => {
    const p = rows.find(x => x.id === id); if (!p) return;
    d.showRows(groups.find(g => g.key === p.status));
    for (const tr of host.querySelectorAll('tbody tr')) {
      const r = Array.isArray(tr._row) ? tr._row[0] : tr._row;
      if (r && r.id === id) { tr.click(); if (tr.scrollIntoView) tr.scrollIntoView({block: 'nearest'}); return; }
    }
  };
}

/* *** A BACKTICK IS MARKUP EVERYWHERE ON THIS PAGE, NOT ONLY IN A DESCRIPTION. ***
   `md()` rendered model descriptions, and every other sentence -- a monitor's reading, a
   suggestion's headline, the question a family asked, this page's own notes -- printed its
   backticks: "`elementary_test_results`\`'s own write history". Reported with a screenshot:
   "markdown is attempted but not rendered in the monitoring tab". Rather than find each of them
   one at a time and miss the next, one observer turns a backticked span into code wherever text
   lands in a panel. Backticks only: an asterisk in a claim ("*** THE YEAR IS THE FACT") is not
   emphasis, and treating it as such would change what the claim says. It builds nodes, never
   HTML, and leaves code, pre and inputs alone. */
const TICK = /`([^`\n]+)`/;
function renderTicks(root) {
  const skip = n => n.closest && n.closest('pre, code, textarea, input, svg');
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: t =>
    TICK.test(t.nodeValue) && t.parentElement && !skip(t.parentElement)
      ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT});
  const hits = [];
  while (walk.nextNode()) hits.push(walk.currentNode);
  for (const t of hits) {
    const parts = t.nodeValue.split(/(`[^`\n]+`)/);
    t.replaceWith(...parts.filter(x => x !== '').map(x => /^`[^`\n]+`$/.test(x)
      ? el('code', {class: 'tick', text: x.slice(1, -1)}) : document.createTextNode(x)));
  }
}
new MutationObserver(ms => { for (const m of ms) for (const n of m.addedNodes) {
  if (n.nodeType === 1) renderTicks(n);
  else if (n.nodeType === 3 && n.parentElement) renderTicks(n.parentElement);
} }).observe(document.querySelector('main'), {childList: true, subtree: true});

/* The tip: one element, shown on hover at once, below the thing it explains and kept inside
   the window. Touch has no hover, so a tap on a tipped element shows it too. */
const TIPBOX = el('div', {class: 'tipbox', hidden: ''});
document.body.append(TIPBOX);
function showTip(t) {
  const txt = t.getAttribute('data-tip');
  if (!txt) return;
  TIPBOX.textContent = txt;
  TIPBOX.hidden = false;
  const r = t.getBoundingClientRect(), w = TIPBOX.offsetWidth, h = TIPBOX.offsetHeight;
  let left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
  let top = r.bottom + 6;
  if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
  TIPBOX.style.left = left + 'px';
  TIPBOX.style.top = top + 'px';
}
document.addEventListener('mouseover', ev => {
  const t = ev.target.closest && ev.target.closest('[data-tip]');
  if (t) showTip(t); else TIPBOX.hidden = true;
});
document.addEventListener('touchstart', ev => {
  const t = ev.target.closest && ev.target.closest('[data-tip]');
  if (t) showTip(t); else TIPBOX.hidden = true;
}, {passive: true});
document.addEventListener('scroll', () => { TIPBOX.hidden = true; }, true);
/* A click is an answer to the tip, so it goes: otherwise the tab's tip sat over the pane it
   had just opened. */
document.addEventListener('mousedown', () => { TIPBOX.hidden = true; }, true);

/* ---------------------------------------------------------------------------------- tabs */
/* Premises by the dbt test that is their declared evidence, so a finding or a monitoring row
   about a test can say what rests on it. */
const PREM_BY_TEST = {};
for (const p of DATA.premises || []) for (const e of p.evidence || []) {
  const m = e.kind === 'declared' && /^`([^`]+)`/.exec(e.detail || '');
  if (m) (PREM_BY_TEST[m[1]] = PREM_BY_TEST[m[1]] || []).push(p);
}
/* "backs the grain of 3 models" -- linked to Guarantees -- or null when nothing rests on it. */
function backsLine(test) {
  const ps = PREM_BY_TEST[test] || [];
  if (!ps.length) return null;
  const grains = new Set(), held = new Set();
  for (const p of ps) for (const u of p.uses || [])
    (u.kind === 'grain' ? grains : held).add(u.model);
  const bits = [grains.size ? 'the grain of ' + num(grains.size) + ' model(s)' : '',
                held.size ? 'findings held back on ' + num(held.size) + ' model(s)' : '']
    .filter(Boolean);
  if (!bits.length) return null;
  const a = el('a', {class: 'lk', href: '#guarantees', text: bits.join(' and ')});
  a.onclick = ev => { ev.preventDefault(); open('guarantees'); GO.guarantees(ps[0].id); };
  return a;
}
/* Premises by the model something about which rests on them, for the Models pane. */
const PREM_BY_MODEL = {};
for (const p of DATA.premises || []) for (const u of p.uses || []) {
  const l = PREM_BY_MODEL[u.model] = PREM_BY_MODEL[u.model] || [];
  if (!l.includes(p)) l.push(p);
}
const VIEWS = {models: modelsTab, chain: chainTab, claims: claimsTab, findings: findingsTab,
               areas: areasTab,
               suggest: suggestTab, monitoring: monitoringTab, guarantees: guaranteesTab,
               answers: answersTab, spend: spendTab, questions: questionsTab, config: configTab,
               understood: understoodTab};
const built = {};
/* *** FOUR SECTIONS, AND A ROW UNDER THEM FOR THE ONE YOU ARE IN. ***
   Explore holds every list the tabs used to be; Settings what is configured; Fix and Decide are
   the review form, embedded once and pointed at a pane. Each section remembers the view you were
   on, so going back to it lands where you left. */
const SEC = JSON.parse(document.getElementById('assay-sections').textContent);
const FORM = String((DATA.meta || {}).form || '');
const DECIDE = [['findings', 'Judgment calls', 'Every call only a person can make, grouped by check.'],
                ['explanations', 'Explanations', 'The kinds of failing row each mart has, in your words.'],
                ['words', 'Words', 'What the words in this warehouse mean here.'],
                ['waivers', 'Waivers', 'Findings accepted on purpose, with the reason.']];
const LAST = {overview: 'understood', explore: 'findings', settings: 'suggest',
              decide: 'decide:findings', fix: 'fix'};
const isFormView = v => v === 'fix' || v.startsWith('decide:') || v === 'form:settings';
function sectionOf(v) {
  if (v === 'understood') return 'overview';
  if (v === 'fix') return 'fix';
  if (v.startsWith('decide:')) return 'decide';
  if (v === 'form:settings') return 'settings';
  for (const [k, vs] of Object.entries(SEC.subviews)) if (vs.includes(v)) return k;
  return 'overview';
}
function paintSub(sec, cur) {
  const host = document.getElementById('subnav');
  let items = [];
  if (sec === 'explore' || sec === 'settings')
    items = SEC.subviews[sec].map(v => [v, SEC.labels[v], SEC.counts[v], SEC.tips[v]]);
  if (sec === 'settings' && FORM)
    items.push(['form:settings', 'Numbers', null,
                'The thresholds this project is judged by. A change goes to audit.yml through the '
                + 'handback, shown as a diff first.']);
  if (sec === 'decide') items = DECIDE.map(([p, l, t]) => ['decide:' + p, l, null, t]);
  host.replaceChildren(...items.map(([v, l, n, tip]) => {
    const b = el('button', {'data-view': v, 'aria-selected': String(v === cur),
                            'data-tip': tip || ''},
                 [document.createTextNode(l),
                  ...(n != null && n !== '' ? [el('b', {text: typeof n === 'number' ? num(n)
                                                                                      : String(n)})]
                                             : [])]);
    b.onclick = () => { dismissCards(); open(v); };
    return b;
  }));
  host.hidden = !items.length;
}
function formView(pane) {
  const host = document.getElementById('p-form');
  if (!FORM) {
    if (!host.firstChild) host.append(el('div', {class: 'formnone'}, [
      el('p', {text: 'Fix and Decide are the review form, and there is none beside this page.'}),
      el('p', {text: '`assay review --emit review.html` writes it; `assay page --form '
                     + 'review.html` links the two.'})]));
    return;
  }
  const src = FORM + '#embed&pane=' + pane;
  let f = host.querySelector('iframe');
  if (!f) { f = el('iframe', {class: 'formframe', src: src, 'aria-label': 'the review form'});
            host.append(f); }
  else if (!f.getAttribute('src').endsWith('pane=' + pane)) f.setAttribute('src', src);
}
function open(name) {
  const sec = sectionOf(name);
  LAST[sec] = name;
  document.querySelectorAll('nav button[data-section]').forEach(b =>
    b.setAttribute('aria-selected', String(b.dataset.section === sec)));
  const cur = document.querySelector('nav button[data-section="' + sec + '"]');
  if (cur) document.getElementById('navcur').textContent = cur.firstChild.textContent;
  paintSub(sec, name);
  const form = isFormView(name);
  document.querySelectorAll('.panel').forEach(p => {
    p.hidden = form ? p.id !== 'p-form' : p.id !== 'p-' + name; });
  if (form) formView(name === 'fix' ? 'fixes' : name === 'form:settings' ? 'settings'
                                                                         : name.slice(7));
  else {
    const host = document.getElementById('p-' + name);
    /* Built once, on first open: a 358-model warehouse renders a view in well under a second,
       and there is no reason to render the ones nobody has looked at. */
    if (!built[name]) { built[name] = 1; VIEWS[name](host); }
  }
  /* *** replaceState THROWS ON file:// IN SOME BROWSERS, AND file:// IS THE POINT. *** */
  try { const h = name.replace(':', '/');
        if (location.hash.slice(1) !== h) history.replaceState(null, '', '#' + h); }
  catch (e) { /* no deep link, and every view still works */ }
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

document.querySelectorAll('nav button[data-section]').forEach(b => {
  b.onclick = () => { dismissCards(); open(LAST[b.dataset.section]); closeMenu(); }; });
/* *** ON A PHONE THE STRIP IS A MENU, NOT ROWS OF TABS. *** One button names the tab you are on,
   and opens the grouped list; picking a tab closes it. */
const NAVM = document.getElementById('navmenu'), NAVEL = document.querySelector('nav');
function closeMenu() { NAVEL.classList.remove('open'); NAVM.setAttribute('aria-expanded', 'false'); }
NAVM.onclick = ev => { ev.stopPropagation(); const on = !NAVEL.classList.contains('open');
  NAVEL.classList.toggle('open', on); NAVM.setAttribute('aria-expanded', String(on)); };
document.addEventListener('click', ev => {
  if (!ev.target.closest('nav') && !ev.target.closest('#navmenu')) closeMenu(); });
{ const h = location.hash.slice(1).replace('/', ':');
  open(VIEWS[h] || isFormView(h) ? h : 'understood'); }
"""
