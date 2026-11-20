/* Slow Diffusion Momentum: walkthrough. No build step, no libraries. */
(function () {
  "use strict";
  const D = window.SITE_DATA;
  const C = D.config;
  const NS = "http://www.w3.org/2000/svg";

  /* ---------- helpers ---------- */
  function h(tag, attrs, html) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === "on") for (const ev in attrs.on) e.addEventListener(ev, attrs.on[ev]);
      else e.setAttribute(k, attrs[k]);
    }
    if (html !== undefined) e.innerHTML = html;
    return e;
  }
  function S(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function svg(w, hgt, label) {
    return S("svg", { viewBox: `0 0 ${w} ${hgt}`, role: "img", "aria-label": label });
  }
  const lin = (d0, d1, r0, r1) => v => r0 + (v - d0) * (r1 - r0) / (d1 - d0);
  const pathOf = pts => pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  function rng(seed) { // mulberry32
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function gauss(r) { let u = 0, v = 0; while (u === 0) u = r(); v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
  const pct = (x, d = 0) => (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) + "%";
  const pctPlain = (x, d = 0) => (x * 100).toFixed(d) + "%";
  const crore = x => "₹" + (x >= 10 ? x.toFixed(0) : x.toFixed(2).replace(/\.?0+$/, "")) + " crore";
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function textCol(o) {
    const col = h("div", { class: "text" });
    if (o.chapter) col.appendChild(h("p", { class: "chapter-name" }, o.chapter));
    col.appendChild(h(o.h1 ? "h1" : "h2", { tabindex: "-1", class: "slide-title" }, o.h1 || o.h2));
    (o.p || []).forEach(t => col.appendChild(t.startsWith("<ul") ? h("div", null, t) : h("p", null, t)));
    if (o.why) col.appendChild(h("p", { class: "why" }, "<strong>Why we do it.</strong> " + o.why));
    if (o.deeper) {
      const d = h("details", { class: "deeper" });
      d.appendChild(h("summary", null, "Under the hood"));
      d.appendChild(h("div", null, o.deeper));
      col.appendChild(d);
    }
    return col;
  }
  function figure(caption) {
    const f = h("div", { class: "figure" });
    f._caption = caption;
    return f;
  }
  function finishFigure(f) { if (f._caption) f.appendChild(h("p", { class: "caption" }, f._caption)); return f; }
  function slider(label, min, max, step, value, onInput, fmt) {
    const id = "s" + Math.random().toString(36).slice(2, 8);
    const row = h("div", { class: "ctrl" });
    const lab = h("label", { for: id }, label);
    const inp = h("input", { type: "range", id, min, max, step, value });
    const out = h("output", { for: id });
    const upd = () => { out.textContent = fmt(+inp.value); onInput(+inp.value); };
    inp.addEventListener("input", upd);
    row.append(lab, inp, out);
    row._init = upd;
    return row;
  }
  function seg(options, initial, onPick, label) {
    const g = h("div", { class: "seg", role: "group", "aria-label": label || "Options" });
    options.forEach(([val, text]) => {
      const b = h("button", { type: "button", "aria-pressed": String(val === initial) }, text);
      b.addEventListener("click", () => {
        g.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        onPick(val);
      });
      g.appendChild(b);
    });
    return g;
  }
  function layout(text, fig) {
    const s = h("section", { class: "slide" });
    s.append(text, fig);
    return s;
  }

  /* ---------- slides ---------- */
  const slides = [];

  /* 0. Cover */
  slides.push({
    chapter: 0, title: "Start",
    render() {
      const text = textCol({
        h1: "Some news takes months to show up in a share price.",
        p: ["This is a walkthrough of a momentum strategy for Indian mid and small-cap shares: what it does, step by step, and why each step is there. No finance background needed."],
      });
      const start = h("button", { class: "cta", type: "button" }, "Start the walkthrough");
      start.addEventListener("click", () => go(1));
      text.appendChild(start);
      text.appendChild(h("p", { class: "byline" }, D.meta.authors.join(" and ") + "<br>" + D.meta.project));

      const f = figure("Illustration of the idea, not real prices.");
      const W = 640, H = 320, x = lin(0, 18, 40, 620), y = lin(95, 135, 280, 30);
      const g = svg(W, H, "A price that adjusts slowly to news, compared with one that adjusts at once");
      const t0 = 2;
      const truth = [], slow = [], fast = [];
      for (let t = 0; t <= 18; t += 0.1) {
        const v = t < t0 ? 100 : 130;
        const s = t < t0 ? 100 : 100 + 30 * (1 - Math.exp(-(t - t0) / 3.6));
        truth.push([x(t), y(v)]); slow.push([x(t), y(s)]);
      }
      fast.push([x(0), y(100)], [x(t0), y(100)], [x(t0), y(130)], [x(18), y(130)]);
      const area = slow.filter(p => p[0] >= x(t0)).concat(truth.filter(p => p[0] >= x(t0)).reverse());
      g.appendChild(S("line", { x1: 40, x2: 620, y1: 280, y2: 280, class: "axis" }));
      g.appendChild(S("path", { d: pathOf(area) + " Z", fill: css("--marigold-soft"), class: "fade-late" }));
      g.appendChild(S("path", { d: pathOf(fast), fill: "none", stroke: css("--slate"), "stroke-width": 2, "stroke-dasharray": "5 5" }));
      const sp = S("path", { d: pathOf(slow), fill: "none", stroke: css("--teal"), "stroke-width": 3.5, "stroke-linecap": "round", class: "draw" });
      g.appendChild(sp);
      g.appendChild(S("line", { x1: x(t0), x2: x(t0), y1: 30, y2: 280, stroke: css("--ink"), "stroke-width": 1, "stroke-dasharray": "2 4" }));
      g.appendChild(S("text", { x: x(t0) + 6, y: 300, class: "t-strong" }, "Good news arrives"));
      g.appendChild(S("text", { x: x(t0) + 10, y: y(130) - 10 }, "If everyone noticed at once, the price would jump"));
      g.appendChild(S("text", { x: x(11), y: y(118), class: "t-strong fade-late" }, "The drift this strategy"));
      g.appendChild(S("text", { x: x(11), y: y(118) + 16, class: "t-strong fade-late" }, "tries to catch"));
      g.appendChild(S("text", { x: x(5.2), y: y(103), fill: css("--teal") }, "When few people are watching, it climbs for months"));
      f.appendChild(g);
      requestAnimationFrame(() => sp.style.setProperty("--len", sp.getTotalLength()));
      return layout(text, finishFigure(f));
    }
  });

  /* 1. What momentum is */
  slides.push({
    chapter: 0, title: "What momentum is",
    render() {
      const text = textCol({
        chapter: "Basics",
        h2: "Momentum: recent winners tend to keep winning, for a while.",
        p: ["Rank every share by how much it rose over the past year. Researchers have found, in many countries and over many decades, that the top of that list tends to keep beating the bottom over the next few months.",
            "It isn't a law. It works on average, across many shares, and it sometimes fails badly. Our job is to use it carefully."],
        deeper: "The founding study is Jegadeesh &amp; Titman (1993). The effect has been found across asset classes and countries (Asness, Moskowitz &amp; Pedersen 2013), including India (Sehgal &amp; Balakrishnan 2002; Ansari &amp; Khan 2012)."
      });
      const f = figure("Made-up prices for 12 shares, for illustration only.");
      const r = rng(7), N = 12, M = 15;
      const paths = [];
      for (let i = 0; i < N; i++) {
        const mu = gauss(r) * 0.025; let v = 100; const p = [v];
        for (let m = 1; m <= M; m++) { v *= 1 + mu + gauss(r) * 0.045; p.push(v); }
        paths.push(p);
      }
      const past = paths.map((p, i) => ({ i, ret: p[12] / p[0] - 1, next: p[15] / p[12] - 1 })).sort((a, b) => b.ret - a.ret);
      const top = new Set(past.slice(0, 3).map(o => o.i)), bot = new Set(past.slice(-3).map(o => o.i));
      const all = paths.flat(), x = lin(0, 15, 40, 620), y = lin(Math.min(...all) * 0.95, Math.max(...all) * 1.03, 280, 20);
      const g = svg(640, 310, "Twelve share prices; the past year's winners and losers are highlighted");
      g.appendChild(S("rect", { x: x(12), y: 15, width: x(15) - x(12), height: 270, fill: css("--teal-soft") }));
      g.appendChild(S("line", { x1: x(12), x2: x(12), y1: 15, y2: 285, stroke: css("--ink"), "stroke-dasharray": "2 4" }));
      g.appendChild(S("text", { x: x(12) - 6, y: 302, "text-anchor": "end", class: "t-strong" }, "Today"));
      g.appendChild(S("text", { x: x(0), y: 302 }, "One year ago"));
      g.appendChild(S("text", { x: x(15), y: 302, "text-anchor": "end" }, "3 months later"));
      const future = [];
      paths.forEach((p, i) => {
        const col = top.has(i) ? css("--teal") : bot.has(i) ? css("--rust") : css("--slate");
        const w = top.has(i) || bot.has(i) ? 2.4 : 1.2, op = top.has(i) || bot.has(i) ? 1 : 0.55;
        g.appendChild(S("path", { d: pathOf(p.slice(0, 13).map((v, m) => [x(m), y(v)])), fill: "none", stroke: col, "stroke-width": w, opacity: op }));
        const fp = S("path", { d: pathOf(p.slice(12).map((v, m) => [x(m + 12), y(v)])), fill: "none", stroke: col, "stroke-width": w, opacity: 0 });
        future.push([fp, op]); g.appendChild(fp);
      });
      f.appendChild(g);
      const avg = s => past.filter(o => s.has(o.i)).reduce((a, o) => a + o.next, 0) / 3;
      const out = h("p", { class: "readout" }, "The past year's top 3 are in teal, the bottom 3 in red.");
      let shown = false;
      const btn = h("button", { class: "small-btn", type: "button" }, "Show the next 3 months");
      btn.addEventListener("click", () => {
        shown = !shown;
        future.forEach(([p, op]) => p.setAttribute("opacity", shown ? op : 0));
        btn.textContent = shown ? "Hide the next 3 months" : "Show the next 3 months";
        out.innerHTML = shown
          ? `Next 3 months: past winners <b>${pct(avg(top), 1)}</b> on average, past losers <b>${pct(avg(bot), 1)}</b>.`
          : "The past year's top 3 are in teal, the bottom 3 in red.";
      });
      f.append(out, btn);
      return layout(text, finishFigure(f));
    }
  });

  /* 2. Why: slow diffusion */
  slides.push({
    chapter: 0, title: "Why it happens",
    render() {
      const text = textCol({
        chapter: "Basics",
        h2: "Why it happens: news spreads slowly where few people are looking.",
        p: ["When a company reports good news, its share price should rise to reflect it. If thousands of analysts and investors follow the company, that happens within hours.",
            "Many Indian mid and small-cap companies have little or no analyst coverage, and until recently few retail investors traded them. There, news can take months to be fully reflected. The price keeps drifting in the same direction, and that drift is what momentum captures."],
        why: "Every later choice follows from this one idea: look where news travels slowly, and measure the slow drift it leaves behind.",
        deeper: "This is the \"slow information diffusion\" explanation of Hong, Lim &amp; Stein (2000), who found momentum strongest in smaller, less-covered firms. Other explanations exist (anchoring, the disposition effect, herding), and we discuss them in the report. Because low retail participation is part of our story, we expect momentum to be weaker after India's post-2020 retail boom, and we check this in the results."
      });
      const f = figure("How a price absorbs the same piece of news at different speeds. Illustration.");
      const W = 640, H = 290, x = lin(-1, 12, 40, 620), y = lin(97, 133, 250, 25);
      const g = svg(W, H, "A price adjusting to news at the chosen speed");
      g.appendChild(S("line", { x1: 40, x2: 620, y1: 250, y2: 250, class: "axis" }));
      g.appendChild(S("line", { x1: x(0), x2: x(0), y1: 25, y2: 250, stroke: css("--ink"), "stroke-dasharray": "2 4" }));
      g.appendChild(S("text", { x: x(0) + 5, y: 270, class: "t-strong" }, "News"));
      g.appendChild(S("text", { x: x(12), y: 270, "text-anchor": "end" }, "12 months later"));
      g.appendChild(S("line", { x1: x(0), x2: x(12), y1: y(130), y2: y(130), stroke: css("--slate"), "stroke-dasharray": "5 5" }));
      g.appendChild(S("text", { x: x(12), y: y(130) - 8, "text-anchor": "end" }, "Value after the news"));
      const area = S("path", { fill: css("--marigold-soft") });
      const line = S("path", { fill: "none", stroke: css("--teal"), "stroke-width": 3 });
      g.append(area, line);
      f.appendChild(g);
      const speeds = [[0.04, "1 day"], [0.25, "1 week"], [1, "1 month"], [2, "2 months"], [3, "3 months"], [5, "5 months"], [8, "8 months"]];
      const out = h("p", { class: "readout" });
      const ctl = slider("How long investors take to notice", 0, 6, 1, 4, i => {
        const tau = speeds[i][0], pts = [[x(-1), y(100)], [x(0), y(100)]];
        for (let t = 0.02; t <= 12; t += 0.05) pts.push([x(t), y(100 + 30 * (1 - Math.exp(-t / tau)))]);
        line.setAttribute("d", pathOf(pts));
        const top = pts.slice(1).map(p => [p[0], y(130)]).reverse();
        area.setAttribute("d", pathOf(pts.slice(1).concat(top)) + " Z");
        const in1 = 1 - Math.exp(-1 / tau);
        out.innerHTML = `One month after the news, <b>${pctPlain(in1)}</b> of it is in the price. The shaded gap is the drift still to come.`;
      }, i => speeds[i][1]);
      f.append(ctl, out); ctl._init();
      return layout(text, finishFigure(f));
    }
  });

  /* 3. Universe */
  slides.push({
    chapter: 1, title: "Where we look",
    render() {
      const text = textCol({
        chapter: "The signal",
        h2: "Where we look: the 400 companies just below the giants.",
        p: ["Rank every listed company by size. We skip the 100 largest, which are followed closely, so news reaches their prices fast. We also skip the tiniest, where prices can be pushed around and are hard to trade.",
            `That leaves ranks 101 to 500: mid and small caps, the same group as the Nifty Midsmallcap 400 index, which is our benchmark.`],
        why: "This is where our idea predicts the slow drift should be, while the shares are still liquid enough for a real fund to buy and sell.",
        deeper: `Size is ranked by market value from April 2019, when the exchange began publishing it, and by daily traded value before that. The top 100 are fixed from the Nifty 50 and Nifty Next 50 lists. Our data covers ${D.facts.companies.toLocaleString("en-IN")} companies from ${D.facts.dataStart} to ${D.facts.dataEnd}, including the ${D.facts.stoppedTrading} that stopped trading. Keeping those avoids flattering the results with only the survivors. The liquidity floor of ₹${C.liquidityFloorCrore} crore a day comes from the fund size: a full position (₹${C.fundCrore} crore ÷ ${C.holdings}) must be buyable within ${C.maxExecutionDays} days at ${pctPlain(C.participation)} of daily trading.`
      });
      const f = figure("Size ranks used in India: SEBI defines large, mid and small caps; NSE's Microcap 250 index covers ranks 501–750.");
      const bands = [
        ["Large", 1, 100, "Followed by many analysts and fund managers. News reaches the price quickly, so there is little slow drift left."],
        ["Mid", 101, 250, "Some coverage, but far less than the largest companies. Included: liquid enough to trade, slow enough to drift."],
        ["Small", 251, 500, "Thin coverage, and historically few retail investors. Where our idea predicts the strongest drift. Included."],
        ["Micro", 501, 750, "The slowest news of all, but prices can be moved by a few traders, and without exchange surveillance data we can't screen that out. Excluded."]
      ];
      const W = 640, x = lin(1, 750, 30, 610);
      const g = svg(W, 150, "Size ranks from 1 to 750, with ranks 101 to 500 highlighted");
      const info = h("p", { class: "readout" });
      bands.forEach(([name, a, b, desc], k) => {
        const ours = k === 1 || k === 2;
        const grp = S("g", { tabindex: "0", role: "button", "aria-label": name + " caps, ranks " + a + " to " + b, style: "cursor:pointer" });
        grp.appendChild(S("rect", { x: x(a), y: 40, width: x(b) - x(a), height: 50, fill: ours ? css("--teal-soft") : css("--grid"), stroke: ours ? css("--teal") : css("--rule"), "stroke-width": ours ? 2 : 1 }));
        grp.appendChild(S("text", { x: (x(a) + x(b)) / 2, y: 70, "text-anchor": "middle", class: "t-strong" }, name));
        grp.appendChild(S("text", { x: x(a), y: 110, "text-anchor": "middle" }, String(a)));
        const show = () => { info.innerHTML = `<b>${name} caps (ranks ${a}–${b}).</b> ${desc}`; };
        grp.addEventListener("click", show); grp.addEventListener("focus", show); grp.addEventListener("mouseenter", show);
        g.appendChild(grp);
        if (k === 1) show();
      });
      g.appendChild(S("text", { x: x(750), y: 110, "text-anchor": "middle" }, "750"));
      g.appendChild(S("path", { d: `M${x(101)} 30 L${x(101)} 22 L${x(500)} 22 L${x(500)} 30`, fill: "none", stroke: css("--teal"), "stroke-width": 1.5 }));
      g.appendChild(S("text", { x: (x(101) + x(500)) / 2, y: 16, "text-anchor": "middle", fill: css("--teal") }, "Our universe: ranks 101–500"));
      g.appendChild(S("text", { x: 30, y: 140 }, "Larger companies"));
      g.appendChild(S("text", { x: 610, y: 140, "text-anchor": "end" }, "Smaller companies"));
      f.append(g, info);
      const ul = h("ul", { class: "checklist" },
        `<li>Shares that keep trading normally (no trade-for-trade surveillance series)</li>
         <li>Listed for at least two years</li>
         <li>Traded on at least 9 days in 10</li>
         <li>Price of at least ₹20</li>
         <li>At least ₹${C.liquidityFloorCrore} crore traded on a typical day</li>
         <li>Not stuck at its daily price limit more than 1 day in 10</li>`);
      f.appendChild(h("p", { class: "readout" }, `After these checks, about <b>${D.facts.eligible.y2012}</b> companies qualify in 2012, <b>${D.facts.eligible.y2013}</b> in 2013 and between <b>${D.facts.eligible.laterMin}</b> and <b>${D.facts.eligible.laterMax}</b> in each later year.`));
      f.appendChild(ul);
      return layout(text, finishFigure(f));
    }
  });

  /* 4. Residual momentum */
  slides.push({
    chapter: 1, title: "Judging against the market",
    render() {
      const text = textCol({
        chapter: "The signal",
        h2: "We judge each company against the market, not on its own.",
        p: ["When the whole market rises, almost every share rises with it. That tells us nothing about any one company's news.",
            "So for each company we first estimate how much it usually moves with the market. Then we subtract that part. What's left, its residual, is the movement caused by its own news. We rank companies on that."],
        why: "Slow diffusion is about company-specific news. Removing the market's move keeps the signal focused on exactly that.",
        deeper: `This is residual momentum (Blitz, Huij &amp; Martens 2011). Each month we regress a company's daily returns on the market's over the past 24 months. Its score is the sum of its residuals over the window, divided by their standard deviation × √days: a t-statistic, so steady company-specific gains score higher than erratic ones. In our universe the market explains a median of only ${D.facts.rSquaredMedian} of a share's daily movement, so most of the variation is company-specific.`
      });
      const f = figure("Two made-up companies over one year.");
      const A = { name: "Company A", beta: 1.3, own: 0.04 }, B = { name: "Company B", beta: 0.5, own: 0.15 };
      const g = svg(640, 230, "Raw and residual returns of two companies");
      const x = lin(-0.3, 0.6, 140, 620);
      f.appendChild(g);
      const out = h("p", { class: "readout" });
      const draw = m => {
        g.innerHTML = "";
        g.appendChild(S("line", { x1: x(0), x2: x(0), y1: 10, y2: 200, class: "axis" }));
        [-0.2, 0, 0.2, 0.4].forEach(v => g.appendChild(S("text", { x: x(v), y: 222, "text-anchor": "middle" }, pct(v))));
        [A, B].forEach((c, k) => {
          const raw = c.beta * m + c.own, y0 = 20 + k * 95;
          g.appendChild(S("text", { x: 0, y: y0 + 30, class: "t-strong" }, c.name));
          [[raw, "Raw return", css("--slate")], [c.own, "Its own news", css("--teal")]].forEach(([v, lab, col], j) => {
            const yy = y0 + j * 32;
            g.appendChild(S("rect", { x: Math.min(x(0), x(v)), y: yy, width: Math.abs(x(v) - x(0)), height: 24, fill: col, rx: 2 }));
            g.appendChild(S("text", { x: v >= 0 ? x(v) + 6 : x(v) - 6, y: yy + 16, "text-anchor": v >= 0 ? "start" : "end", class: "t-ink" }, lab + " " + pct(v, 0)));
          });
        });
        const rawWinner = A.beta * m + A.own > B.beta * m + B.own ? "A" : "B";
        out.innerHTML = `On raw returns, <b>Company ${rawWinner}</b> ranks higher. On its own news, <b>Company B</b> ranks higher whatever the market does.`;
      };
      const ctl = slider("Market's move over the year", -20, 40, 5, 25, v => draw(v / 100), v => pct(v / 100));
      f.append(ctl, out); ctl._init();
      return layout(text, finishFigure(f));
    }
  });

  /* 5. Windows */
  slides.push({
    chapter: 1, title: "How far back we look",
    render() {
      const text = textCol({
        chapter: "The signal",
        h2: "How far back we look: the past year, with the last month left out.",
        p: ["We measure each company's own-news drift over two windows: the past year, and the past six months. Then we average the two scores.",
            "Months two to seven fall inside both windows, so recent drift counts more. We ignore the most recent month entirely."],
        why: "News from the last six months is only partly in the price, so more of its drift is still to come. The latest month is skipped because very short-term moves tend to reverse rather than continue.",
        deeper: "Windows: months 12 to 2 and months 7 to 2 before each decision, weighted 50/50. NSE's own momentum indices also blend 6- and 12-month measures. Short-term reversal is documented by Jegadeesh (1990); in small caps it is driven by bid-ask bounce and shares released from their daily price limits."
      });
      const f = figure("Each square is one month before today's decision. Point at a month to see how it's used.");
      const g = svg(640, 200, "Twelve months before the decision and how each is used");
      const cw = 46, x0 = 40, info = h("p", { class: "readout" });
      for (let k = 12; k >= 1; k--) {
        const i = 12 - k, xx = x0 + i * (cw + 2);
        const twice = k >= 2 && k <= 7, skipped = k === 1;
        const fill = skipped ? css("--grid") : twice ? css("--teal") : css("--teal-soft");
        const rc = S("rect", { x: xx, y: 40, width: cw, height: 56, fill, stroke: skipped ? css("--marigold") : css("--rule"), "stroke-width": skipped ? 2 : 1, "stroke-dasharray": skipped ? "4 3" : "", tabindex: "0", rx: 3 });
        const msg = skipped ? "<b>Last month:</b> skipped. Very recent moves tend to reverse."
          : twice ? `<b>${k} months ago:</b> inside both windows, so it counts twice.`
          : `<b>${k} months ago:</b> inside the one-year window only.`;
        const show = () => { info.innerHTML = msg; };
        rc.addEventListener("mouseenter", show); rc.addEventListener("focus", show); rc.addEventListener("click", show);
        g.appendChild(rc);
        g.appendChild(S("text", { x: xx + cw / 2, y: 74, "text-anchor": "middle", class: twice ? "" : "t-ink", fill: twice ? css("--paper") : "" }, String(k)));
      }
      g.appendChild(S("text", { x: x0, y: 28 }, "12 months ago"));
      g.appendChild(S("text", { x: x0 + 12 * (cw + 2) - 2, y: 28, "text-anchor": "end" }, "Last month"));
      const bar = (from, to, yy, lab) => {
        const a = x0 + (12 - from) * (cw + 2), b = x0 + (12 - to) * (cw + 2) + cw;
        g.appendChild(S("path", { d: `M${a} ${yy - 6} L${a} ${yy} L${b} ${yy} L${b} ${yy - 6}`, fill: "none", stroke: css("--ink"), "stroke-width": 1.5 }));
        g.appendChild(S("text", { x: a, y: yy + 18, class: "t-ink" }, lab));
      };
      bar(12, 2, 128, "One-year window: months 12 to 2");
      bar(7, 2, 172, "Six-month window: months 7 to 2");
      f.append(g, info);
      info.innerHTML = "<b>Darker months</b> count twice. The dashed month is skipped.";
      return layout(text, finishFigure(f));
    }
  });

  /* 6. Two checks */
  slides.push({
    chapter: 1, title: "Two quality checks",
    render() {
      const text = textCol({
        chapter: "The signal",
        h2: "Two checks before a share can be bought.",
        p: ["<b>It must actually be rising.</b> Judging against the market can rank a share highly just because it fell less than everyone else. A fund for family and friends should own shares that are going up.",
            "<b>Its rise must be steady.</b> We prefer shares that climbed through many small up-days over ones carried by a few big jumps, which in small caps are often one-off announcements or price-limit spikes."],
        why: "Steady climbs are the fingerprint of news arriving gradually, which is exactly the slow drift we're after.",
        deeper: `The steadiness check is the "frog in the pan" measure of Da, Gurun &amp; Warachka (2014): sign of the return × (% of down days − % of up days) over months 12 to 2. We drop shares scoring above 0. That measure flips meaning for shares that fell, which is a second reason for the rising check. Combining relative and absolute strength also does better in India (Sehgal, Pandey &amp; Sen 2024). When few shares pass, the fund holds fewer, larger positions (as few as ${D.facts.minCandidates} in our data) rather than cash.`
      });
      const f = figure("Made-up daily prices over one year.");
      const g = svg(640, 220, "A share price path and its check results");
      const stats = h("table", { class: "mini-table" });
      f.append(g, stats);
      const make = (kind, seed) => {
        const r = rng(seed), n = 250, rets = [];
        for (let d = 0; d < n; d++) {
          if (kind === "steady") rets.push(0.0012 + gauss(r) * 0.009);
          else if (kind === "jumpy") rets.push(-0.0016 + gauss(r) * 0.008 + (d % 62 === 30 ? 0.11 : 0));
          else rets.push(-0.0005 + gauss(r) * 0.011);
        }
        const target = kind === "fell" ? Math.log(0.9) : Math.log(1.3);
        const sum = rets.reduce((a, b) => a + Math.log(1 + b), 0), adj = (target - sum) / n;
        return rets.map(v => Math.exp(Math.log(1 + v) + adj) - 1);
      };
      let seed = 3, kind = "steady";
      const draw = () => {
        const rets = make(kind, seed);
        let v = 100; const pts = [[0, v]];
        rets.forEach((q, d) => { v *= 1 + q; pts.push([d + 1, v]); });
        const vals = pts.map(p => p[1]), x = lin(0, 250, 40, 620), y = lin(Math.min(...vals) * 0.97, Math.max(...vals) * 1.02, 200, 15);
        g.innerHTML = "";
        g.appendChild(S("line", { x1: 40, x2: 620, y1: y(100), y2: y(100), stroke: css("--slate"), "stroke-dasharray": "3 4" }));
        g.appendChild(S("text", { x: 620, y: y(100) + 16, "text-anchor": "end" }, "Starting price"));
        g.appendChild(S("path", { d: pathOf(pts.map(p => [x(p[0]), y(p[1])])), fill: "none", stroke: css("--teal"), "stroke-width": 2 }));
        const up = rets.filter(q => q > 0).length / rets.length, dn = rets.filter(q => q < 0).length / rets.length;
        const total = vals[vals.length - 1] / 100 - 1, id = Math.sign(total) * (dn - up);
        const rising = total > 0, smooth = id <= 0;
        const badge = ok => `<span class="badge ${ok ? "pass" : "fail"}">${ok ? "Pass" : "Fail"}</span>`;
        stats.innerHTML = `<tr><th>Return over the year</th><td>${pct(total)}</td></tr>
          <tr><th>Up days / down days</th><td>${pctPlain(up)} / ${pctPlain(dn)}</td></tr>
          <tr><th>Check 1: actually rising</th><td>${badge(rising)}</td></tr>
          <tr><th>Check 2: steady rise (score ${id.toFixed(2)}, must be 0 or less)</th><td>${rising ? badge(smooth) : "not checked"}</td></tr>
          <tr><th>Can be bought</th><td>${badge(rising && smooth)}</td></tr>`;
      };
      const picker = seg([["steady", "Steady climber"], ["jumpy", "Carried by jumps"], ["fell", "Fell, but less than the market"]], "steady", k => { kind = k; draw(); }, "Example share");
      const again = h("button", { class: "small-btn", type: "button" }, "Another example");
      again.addEventListener("click", () => { seed += 1; draw(); });
      const row = h("div"); row.append(picker, document.createTextNode(" "), again);
      f.insertBefore(row, g);
      draw();
      return layout(text, finishFigure(f));
    }
  });

  /* 7. Picking 30 and the buffer */
  slides.push({
    chapter: 2, title: "Picking the shares",
    render() {
      const text = textCol({
        chapter: "Building the fund",
        h2: `We buy the top ${C.holdings}, and only sell when a share drops below ${C.sellBelowRank}.`,
        p: [`Each month every eligible share gets a score, and we rank them. A share enters the fund when it reaches the top ${C.holdings}.`,
            `Once we own it, we keep it until it falls below rank ${C.sellBelowRank}. A share slipping from 28th to 35th is probably still drifting, and selling it would just cost money.`],
        why: `${C.holdings} shares is a portfolio our investors can follow, where one share collapsing by half costs the fund under 2%. The gap between "buy" and "sell" cuts trading a lot.`,
        deeper: `The sell rule is a "buffer" of 1.5 × ${C.holdings} = ${C.sellBelowRank}. Novy-Marx &amp; Velikov (2016, 2019) find buffering the most effective way to cut momentum's trading costs. NSE's 30-stock momentum index uses a similar buffer. We also test 40 holdings on the training data.`
      });
      const f = figure("Ranks 1 to 100. Move the share and choose whether we already own it.");
      const g = svg(640, 130, "Ranking strip with buy, hold and sell zones");
      const x = lin(1, 100, 30, 610);
      const zones = [[1, C.holdings, css("--teal-soft"), css("--teal"), "Buy zone"], [C.holdings + 1, C.sellBelowRank, css("--marigold-soft"), css("--marigold"), "Keep if owned"], [C.sellBelowRank + 1, 100, css("--grid"), css("--rule"), "Sell or ignore"]];
      zones.forEach(([a, b, fill, stroke, lab]) => {
        g.appendChild(S("rect", { x: x(a) - 2.9, y: 40, width: x(b) - x(a) + 5.8, height: 40, fill, stroke }));
        g.appendChild(S("text", { x: (x(a) + x(b)) / 2, y: 30, "text-anchor": "middle", class: "t-ink" }, lab));
      });
      [1, C.holdings, C.sellBelowRank, 100].forEach(v => g.appendChild(S("text", { x: x(v), y: 100, "text-anchor": "middle" }, String(v))));
      const marker = S("circle", { cy: 60, r: 9, fill: css("--ink") });
      g.appendChild(marker);
      f.appendChild(g);
      const out = h("p", { class: "readout" });
      let owned = false, rank = 38;
      const upd = () => {
        marker.setAttribute("cx", x(rank));
        let msg;
        if (rank <= C.holdings) msg = owned ? "Keep it: still in the top " + C.holdings + "." : "<b>Buy it:</b> it has entered the top " + C.holdings + ".";
        else if (rank <= C.sellBelowRank) msg = owned ? "<b>Keep it:</b> it slipped, but not below " + C.sellBelowRank + "." : "Don't buy yet: not in the top " + C.holdings + ".";
        else msg = owned ? "<b>Sell it:</b> it has fallen below rank " + C.sellBelowRank + "." : "Ignore it.";
        out.innerHTML = msg;
      };
      const ctl = slider("This share's rank", 1, 100, 1, rank, v => { rank = v; upd(); }, v => "#" + v);
      const tog = seg([[false, "We don't own it yet"], [true, "We already own it"]], false, v => { owned = v; upd(); }, "Ownership");
      f.append(tog, ctl, out); ctl._init();
      return layout(text, finishFigure(f));
    }
  });

  /* 8. Weights */
  slides.push({
    chapter: 2, title: "How much of each",
    render() {
      const text = textCol({
        chapter: "Building the fund",
        h2: "Calmer shares get more money; jumpy ones get less.",
        p: ["We don't split the money equally. Each share's weight is set by how much its price usually swings: the steadier the share, the bigger the position.",
            `No single share can be more than ${pctPlain(C.maxWeight)} of the fund at a rebalance.`],
        why: "Each share then adds a similar amount of risk, so no single erratic small cap can sink the fund, while every top-ranked idea still gets a place.",
        deeper: `Weights are proportional to 1 ÷ volatility over the past six months, capped at ${pctPlain(C.maxWeight)}, with the excess spread across the rest. Volatility enters twice in our design: the score divides by company-specific volatility, and the weights by total volatility. Both lean the fund towards steadier shares, which we report.`
      });
      const f = figure(`All ${C.holdings} positions, sorted from calmest to jumpiest. Made-up volatilities.`);
      const r = rng(11);
      const vols = Array.from({ length: C.holdings - 1 }, () => 0.2 + r() * 0.45);
      const g = svg(640, 250, "Portfolio weights by volatility");
      f.appendChild(g);
      const out = h("p", { class: "readout" });
      const weights = vs => {
        const raw = vs.map(v => 1 / v); let w = raw.map(v => v / raw.reduce((a, b) => a + b, 0));
        for (let it = 0; it < 20; it++) {
          const over = w.map(v => v > C.maxWeight);
          if (!over.some(Boolean)) break;
          const capped = over.filter(Boolean).length * C.maxWeight;
          const freeRaw = raw.reduce((a, v, i) => a + (over[i] ? 0 : v), 0);
          w = w.map((v, i) => over[i] ? C.maxWeight : raw[i] / freeRaw * (1 - capped));
        }
        return w;
      };
      const ctl = slider("Highlighted share's yearly swings", 10, 80, 1, 30, v => {
        const all = vols.map((vv, i) => ({ v: vv, me: false })).concat([{ v: v / 100, me: true }]);
        const w = weights(all.map(o => o.v));
        all.forEach((o, i) => { o.w = w[i]; });
        all.sort((a, b) => a.v - b.v);
        const x0 = 40, bw = 560 / all.length, y = lin(0, 0.08, 210, 20);
        g.innerHTML = "";
        g.appendChild(S("line", { x1: 40, x2: 610, y1: 210, y2: 210, class: "axis" }));
        g.appendChild(S("line", { x1: 40, x2: 610, y1: y(C.maxWeight), y2: y(C.maxWeight), stroke: css("--rust"), "stroke-dasharray": "5 4" }));
        g.appendChild(S("text", { x: 610, y: y(C.maxWeight) - 6, "text-anchor": "end", fill: css("--rust") }, `Cap: ${pctPlain(C.maxWeight)}`));
        g.appendChild(S("line", { x1: 40, x2: 610, y1: y(1 / C.holdings), y2: y(1 / C.holdings), stroke: css("--slate"), "stroke-dasharray": "2 4" }));
        g.appendChild(S("text", { x: 44, y: y(1 / C.holdings) - 6 }, `Equal split: ${pctPlain(1 / C.holdings, 1)}`));
        all.forEach((o, i) => g.appendChild(S("rect", { x: x0 + i * bw + 1, y: y(o.w), width: bw - 2, height: 210 - y(o.w), fill: o.me ? css("--marigold") : css("--teal"), opacity: o.me ? 1 : 0.75 })));
        g.appendChild(S("text", { x: 40, y: 232 }, "Calmest"));
        g.appendChild(S("text", { x: 610, y: 232, "text-anchor": "end" }, "Jumpiest"));
        const me = all.find(o => o.me);
        out.innerHTML = `The highlighted share swings about <b>${pctPlain(me.v)}</b> a year and gets <b>${pctPlain(me.w, 1)}</b> of the fund.`;
      }, v => v + "%");
      f.append(ctl, out); ctl._init();
      return layout(text, finishFigure(f));
    }
  });

  /* 9. Trading realistically */
  slides.push({
    chapter: 2, title: "Trading like a real fund",
    render() {
      const pos = C.fundCrore / C.holdings;
      const text = textCol({
        chapter: "Building the fund",
        h2: "Buying slowly, the way a real fund has to.",
        p: [`The fund manages ₹${C.fundCrore} crore, so a typical position is about ${crore(pos)}. In a thinly traded small cap, buying that much at once would push the price up against us.`,
            `So we never buy more than ${pctPlain(C.participation)} of a share's usual daily trading, and we spread a purchase over up to ${C.maxExecutionDays} days. Shares stuck at their daily price limit can't be traded at all, and the back-test respects that.`],
        why: `Our edge plays out over months, so taking two weeks to build a position loses little of it, and paying less to trade keeps more of the return.`,
        deeper: `Trades are priced at each day's average traded price (VWAP), from the day after the decision. Costs: brokerage of ${C.brokerageBps} bps, between SEBI's caps on mutual-fund brokerage (12 bps for most of our sample, 6 bps from December 2025); the bid-ask spread, estimated for each share from its daily prices (Abdi &amp; Ranaldo 2017); taxes and exchange fees at their legal rates; and market impact = k × daily volatility × √(order ÷ daily trading), with k = ${C.impactK} and robustness checks at 0.5 and 2.`
      });
      const f = figure(`One ${crore(pos)} purchase. Each square is a trading day; the order lapses after ${C.maxExecutionDays}.`);
      const g = svg(640, 90, "Days needed to complete a purchase");
      f.appendChild(g);
      const out = h("p", { class: "readout" });
      let adv = 3, vol = 2.5;
      const upd = () => {
        const perDay = C.participation * adv, days = pos / perDay;
        g.innerHTML = "";
        const cw = 38;
        for (let d = 0; d < C.maxExecutionDays; d++) {
          const fillFrac = Math.max(0, Math.min(1, days - d));
          g.appendChild(S("rect", { x: 10 + d * (cw + 3), y: 20, width: cw, height: 38, fill: css("--grid"), stroke: css("--rule"), rx: 3 }));
          if (fillFrac > 0 && adv >= C.liquidityFloorCrore) g.appendChild(S("rect", { x: 10 + d * (cw + 3), y: 20, width: cw * fillFrac, height: 38, fill: css("--teal"), rx: 3 }));
        }
        g.appendChild(S("text", { x: 10, y: 80 }, "Day 1"));
        g.appendChild(S("text", { x: 10 + (C.maxExecutionDays - 1) * (cw + 3) + cw, y: 80, "text-anchor": "end" }, "Day " + C.maxExecutionDays));
        const part = Math.min(C.participation, pos / adv);
        const impactBps = C.impactK * (vol / 100) * Math.sqrt(part) * 10000;
        let msg;
        if (adv < C.liquidityFloorCrore) msg = `<b>Not eligible.</b> This share trades less than ₹${C.liquidityFloorCrore} crore a day, so we couldn't build a full position in ${C.maxExecutionDays} days.`;
        else if (days > C.maxExecutionDays) msg = `Only <b>${pctPlain(C.maxExecutionDays / days)}</b> gets bought before the order lapses.`;
        else msg = `Fully bought in <b>${Math.ceil(days)} day${Math.ceil(days) > 1 ? "s" : ""}</b>. Estimated price impact about <b>${impactBps.toFixed(0)} bps</b>, plus ${C.brokerageBps} bps brokerage, the spread and taxes.`;
        out.innerHTML = msg;
      };
      const advVals = [0.5, 0.8, 1.1, 1.5, 2, 3, 5, 8, 12, 20, 30];
      const c1 = slider("Traded on a typical day", 0, advVals.length - 1, 1, 5, i => { adv = advVals[i]; upd(); }, i => crore(advVals[i]));
      const c2 = slider("Daily price swing", 1, 4, 0.5, 2.5, v => { vol = v; upd(); }, v => v + "%");
      f.append(c1, c2, out); c1._init(); c2._init();
      return layout(text, finishFigure(f));
    }
  });

  /* 10. Risk layers */
  slides.push({
    chapter: 3, title: "Four safety limits",
    render() {
      const text = textCol({
        chapter: "Staying safe",
        h2: "Four safety limits decide how much of the fund is invested.",
        p: ["Each limit can cut how much of the fund is in shares, with the rest in cash. They don't add up: on any day, the strictest one wins.",
            "Try a calm market, then a panic, then a fund that has fallen from its peak."],
        why: "The aim is a consistent return generator. These limits trim risk when markets turn rough, but never go fully to cash, because momentum's worst losses tend to come in sharp rebounds after a crash.",
        deeper: `<b>Volatility target:</b> exposure = ${pctPlain(C.volTarget, 2)} ÷ recent volatility (6 months), between ${pctPlain(C.volFloor)} and 100%. The target is the benchmark's own volatility over 2012–16 (Barroso &amp; Santa-Clara 2015; Moreira &amp; Muir 2017). <b>VaR and Expected Shortfall:</b> 99% one-day VaR at most ${pctPlain(C.varLimit, 1)} of the fund, 97.5% ES at most ${pctPlain(C.esLimit)}. <b>Drawdown tiers:</b> half invested at ${pctPlain(C.softDD)} below the peak, ${pctPlain(C.hardExposure)} at ${pctPlain(C.hardDD)} for at least 42 days. Final exposure is 99% × the smallest of the four, because of a 1% cash buffer. The VaR and ES bars here use a simple normal approximation; the back-test uses 500 days of history.`
      });
      const f = figure("How the limits combine. A simplified illustration of the real rules.");
      const g = svg(640, 250, "Exposure allowed by each limit and the final exposure");
      f.appendChild(g);
      const out = h("p", { class: "readout" });
      let vol = 0.17, dd = 0;
      const upd = () => {
        const daily = vol / Math.sqrt(252);
        const layers = [
          ["Volatility target", Math.max(C.volFloor, Math.min(1, C.volTarget / vol))],
          ["VaR limit", Math.min(1, C.varLimit / (2.33 * daily * 1.15))],
          ["Expected Shortfall limit", Math.min(1, C.esLimit / (2.34 * daily * 1.25))],
          ["Drawdown tier", dd >= C.hardDD ? C.hardExposure : dd >= C.softDD ? C.softExposure : 1]
        ];
        const minV = Math.min(...layers.map(l => l[1])), final = (1 - C.cashBuffer) * minV;
        const binding = layers.findIndex(l => l[1] === minV);
        const x = lin(0, 1, 200, 610);
        g.innerHTML = "";
        layers.forEach(([name, v], i) => {
          const yy = 10 + i * 42;
          g.appendChild(S("text", { x: 0, y: yy + 17, class: i === binding ? "t-strong" : "" }, name));
          g.appendChild(S("rect", { x: x(0), y: yy, width: x(1) - x(0), height: 24, fill: css("--grid"), rx: 2 }));
          g.appendChild(S("rect", { x: x(0), y: yy, width: x(v) - x(0), height: 24, fill: i === binding ? css("--marigold") : css("--slate"), rx: 2 }));
          g.appendChild(S("text", { x: x(v) + 6 > 580 ? x(v) - 6 : x(v) + 6, y: yy + 17, "text-anchor": x(v) + 6 > 580 ? "end" : "start", class: "t-ink" }, pctPlain(v)));
        });
        const yy = 190;
        g.appendChild(S("text", { x: 0, y: yy + 22, class: "t-strong" }, "Invested in shares"));
        g.appendChild(S("rect", { x: x(0), y: yy, width: x(1) - x(0), height: 34, fill: css("--grid"), rx: 2 }));
        g.appendChild(S("rect", { x: x(0), y: yy, width: x(final) - x(0), height: 34, fill: css("--teal"), rx: 2 }));
        g.appendChild(S("text", { x: x(final) + 8, y: yy + 22, class: "t-strong" }, pctPlain(final)));
        out.innerHTML = `The <b>${layers[binding][0].toLowerCase()}</b> is the strictest right now, so it sets the level.`;
      };
      const c1 = slider("Market turbulence (yearly swings)", 8, 80, 1, 17, v => { vol = v / 100; upd(); }, v => v + "%");
      const c2 = slider("Fund's fall from its peak", 0, 35, 1, 0, v => { dd = v / 100; upd(); }, v => v ? "−" + v + "%" : "at peak");
      f.append(c1, c2, out); c1._init(); c2._init();
      return layout(text, finishFigure(f));
    }
  });

  /* 11. Train / test and stress */
  slides.push({
    chapter: 4, title: "Testing on unseen years",
    render() {
      const text = textCol({
        chapter: "Honest testing",
        h2: "We set the rules on old data, then test once on years the rules never saw.",
        p: [`Every choice was fixed using ${C.trainStart} to ${C.trainEnd}. The rules were then frozen and run exactly once on ${C.testStart} to ${C.testEnd}. No changes afterwards.`,
            "The test years include every market crash the course asked us to study, so the strategy meets them for the first time, just as a real fund would."],
        why: "A strategy tuned on the same years it's judged on will always look good. Keeping ten years unseen is the honest way to find out whether it works.",
        deeper: `Training has 58 monthly decisions; the test has 117. The years from 2010 to early 2012 are used only to build up the 24 months of history the signal needs. Crash dates come from the index data (python run.py episodes), not from memory. The Covid rebound (from late March 2020) is our "momentum crash" test: momentum strategies have historically lost most when beaten-down shares rally sharply (Daniel &amp; Moskowitz 2016).`
      });
      const f = figure("Point at a crash to see how deep it was. Falls are peak to trough in the Nifty Smallcap 100, plus Covid in the Nifty 500.");
      const x = lin(2010, 2027, 20, 620), toY = s => { const [yy, mm] = s.split("-").map(Number); return yy + (mm - 1) / 12; };
      const g = svg(640, 230, "Timeline of training years, test years and market crashes");
      g.appendChild(S("rect", { x: x(2010), y: 30, width: x(2012.08) - x(2010), height: 40, fill: css("--grid"), stroke: css("--rule") }));
      g.appendChild(S("rect", { x: x(2012.08), y: 30, width: x(2017) - x(2012.08), height: 40, fill: css("--teal-soft"), stroke: css("--teal") }));
      g.appendChild(S("rect", { x: x(2017), y: 30, width: x(2026.83) - x(2017), height: 40, fill: css("--panel"), stroke: css("--ink"), "stroke-width": 1.5 }));
      g.appendChild(S("text", { x: (x(2010) + x(2012.08)) / 2, y: 55, "text-anchor": "middle" }, "History"));
      g.appendChild(S("text", { x: (x(2012.08) + x(2017)) / 2, y: 55, "text-anchor": "middle", class: "t-strong" }, "Training"));
      g.appendChild(S("text", { x: (x(2017) + x(2026.83)) / 2, y: 55, "text-anchor": "middle", class: "t-strong" }, "Test: run once"));
      [2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026].forEach(yy => g.appendChild(S("text", { x: x(yy), y: 20, "text-anchor": "middle" }, String(yy))));
      const info = h("p", { class: "readout" }, "Point at a red bar.");
      g.appendChild(S("line", { x1: x(2020.23), x2: x(2020.23), y1: 85, y2: 200, stroke: css("--marigold"), "stroke-width": 2 }));
      D.episodes.forEach((e, i) => {
        const a = x(toY(e.start)), b = Math.max(x(toY(e.end) + 1 / 12), a + 6), yy = 95 + (i % 2) * 34;
        const rc = S("rect", { x: a, y: yy, width: b - a, height: 22, fill: css("--rust"), opacity: 0.85, rx: 2, tabindex: "0" });
        const msg = `<b>${e.name}, ${e.start.replace("-", "/")} to ${e.end.replace("-", "/")}:</b> ${pct(e.fall)} from peak to trough${toY(e.start) < 2017 ? " (training years)" : " (test years)"}.`;
        const show = () => { info.innerHTML = msg; };
        rc.addEventListener("mouseenter", show); rc.addEventListener("focus", show); rc.addEventListener("click", show);
        g.appendChild(rc);
        g.appendChild(S("text", { x: b + 4, y: yy + 16, class: "t-ink" }, pct(e.fall)));
      });
      g.appendChild(S("text", { x: x(2020.23) - 5, y: 215, "text-anchor": "end", fill: css("--marigold"), class: "t-strong" }, "Covid rebound: the momentum-crash test"));
      f.append(g, info);
      return layout(text, finishFigure(f));
    }
  });

  /* 12. Luck */
  slides.push({
    chapter: 4, title: "Why we tried so few versions",
    render() {
      const text = textCol({
        chapter: "Honest testing",
        h2: "Try enough versions and one will look brilliant by luck alone.",
        p: ["Each dot below is a strategy with no real edge at all: its returns are pure coin-flips. Measured over five years, some still look impressive.",
            "The more versions you try, the better the best one looks, and the more likely it disappoints on new data. So we chose almost everything from reasoning, and let the data decide only a few settings."],
        why: `Our total, including an earlier design we dropped, is ${C.trials} versions. Fewer trials means the result we report is more believable.`,
        deeper: "The course requires us to report how many versions we tried. We then test our Sharpe ratio against the best one that luck alone would be expected to produce after that many tries (the deflated Sharpe ratio of Bailey &amp; López de Prado 2014). Published strategies are, on average, weaker after publication than in the original studies (McLean &amp; Pontiff 2016)."
      });
      const f = figure("Simulated: 60 months of random returns per strategy, zero true edge. Sharpe ratio = return per unit of risk.");
      const g = svg(640, 180, "Sharpe ratios of random strategies, with the best highlighted");
      f.appendChild(g);
      const out = h("p", { class: "readout" });
      let seed = 21, n = C.trials, pool = [];
      const sim = () => {
        const r = rng(seed); pool = [];
        for (let k = 0; k < 300; k++) {
          const xs = Array.from({ length: 60 }, () => gauss(r) * 0.05);
          const m = xs.reduce((a, b) => a + b, 0) / 60, sd = Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / 59);
          pool.push({ s: m / sd * Math.sqrt(12), j: r() });
        }
      };
      const draw = () => {
        const x = lin(-1.5, 2, 30, 610), use = pool.slice(0, n), best = use.reduce((a, b) => (b.s > a.s ? b : a));
        g.innerHTML = "";
        g.appendChild(S("line", { x1: 30, x2: 610, y1: 130, y2: 130, class: "axis" }));
        [-1, 0, 1, 2].forEach(v => g.appendChild(S("text", { x: x(v), y: 150, "text-anchor": "middle" }, v.toFixed(0))));
        g.appendChild(S("line", { x1: x(0), x2: x(0), y1: 20, y2: 130, stroke: css("--slate"), "stroke-dasharray": "2 4" }));
        g.appendChild(S("text", { x: x(0) + 4, y: 170 }, "True edge: zero"));
        use.forEach(o => g.appendChild(S("circle", { cx: x(Math.max(-1.5, Math.min(2, o.s))), cy: 30 + o.j * 90, r: o === best ? 7 : 4, fill: o === best ? css("--marigold") : css("--teal"), opacity: o === best ? 1 : 0.55 })));
        out.innerHTML = `Best of <b>${n}</b>: a Sharpe ratio of <b>${best.s.toFixed(2)}</b>, from luck alone.`;
      };
      const ctl = slider("Versions tried", 1, 300, 1, n, v => { n = v; draw(); }, v => String(v));
      const again = h("button", { class: "small-btn", type: "button" }, "Run again");
      again.addEventListener("click", () => { seed += 1; sim(); draw(); });
      sim();
      f.append(ctl, out, again); ctl._init();
      return layout(text, finishFigure(f));
    }
  });

  /* 13. Results */
  slides.push({
    chapter: 4, title: "Results",
    render() {
      const R = D.results;
      if (!R) {
        const text = textCol({
          chapter: "Honest testing",
          h2: "Results",
          p: ["The one-time test run hasn't happened yet. When it has, this page will show exactly what it produced, good or bad, with nothing re-run or adjusted."],
        });
        const f = h("div", { class: "pending" });
        f.innerHTML = `<p class="readout">What will appear here</p>
          <ul class="checklist">
            <li>Growth of ₹1 invested, against the Nifty Midsmallcap 400 total-return index</li>
            <li>Yearly return, volatility, Sharpe ratio and worst fall, for the fund and the index</li>
            <li>How much trading costs took out each year</li>
            <li>How the fund did in each crash, including the Covid rebound</li>
            <li>How likely the Sharpe ratio is to be more than luck, given the ${C.trials} versions we tried</li>
            <li>Before and after 2020, to test our retail-boom prediction</li>
          </ul>`;
        return layout(text, f);
      }
      const text = textCol({
        chapter: "Honest testing",
        h2: "What happened in the test years",
        p: [`${R.period}. Every figure comes from the single frozen test run, after all costs.`],
        deeper: `Given the ${C.trials} versions we tried, the probability that the fund's true Sharpe ratio is better than the best that luck alone would produce is ${R.deflatedSharpe != null ? pctPlain(R.deflatedSharpe) : "not available"} (deflated Sharpe ratio). Full tables are in the repository's results folder.`
      });
      const f = figure(`Growth of ₹1: fund (teal) against the ${R.benchmarkName} (grey).`);
      const s = R.series, all = s.strategy.concat(s.benchmark);
      const x = lin(0, s.dates.length - 1, 40, 620), y = lin(Math.min(...all) * 0.95, Math.max(...all) * 1.05, 230, 15);
      const g = svg(640, 260, "Growth of one rupee in the fund and the benchmark");
      g.appendChild(S("line", { x1: 40, x2: 620, y1: 230, y2: 230, class: "axis" }));
      g.appendChild(S("path", { d: pathOf(s.benchmark.map((v, i) => [x(i), y(v)])), fill: "none", stroke: css("--slate"), "stroke-width": 2 }));
      g.appendChild(S("path", { d: pathOf(s.strategy.map((v, i) => [x(i), y(v)])), fill: "none", stroke: css("--teal"), "stroke-width": 2.5 }));
      g.appendChild(S("text", { x: 40, y: 250 }, s.dates[0]));
      g.appendChild(S("text", { x: 620, y: 250, "text-anchor": "end" }, s.dates[s.dates.length - 1]));
      f.appendChild(g);
      const m = R.metrics, row = (lab, a, b, fmt) => `<tr><td>${lab}</td><td>${a == null ? "–" : fmt(a)}</td><td>${b == null ? "–" : fmt(b)}</td></tr>`;
      f.appendChild(h("table", { class: "mini-table" },
        `<tr><th></th><th>Fund</th><th>Index</th></tr>` +
        row("Yearly return", m.strategy.cagr, m.benchmark.cagr, v => pct(v, 1)) +
        row("Volatility", m.strategy.vol, m.benchmark.vol, v => pctPlain(v, 1)) +
        row("Sharpe ratio", m.strategy.sharpe, m.benchmark.sharpe, v => v.toFixed(2)) +
        row("Worst fall", m.strategy.maxDrawdown, m.benchmark.maxDrawdown, v => pct(v, 1)) +
        row("Cost drag per year", m.strategy.costDrag, null, v => pctPlain(v, 2))));
      if (R.stress && R.stress.length) {
        f.appendChild(h("table", { class: "mini-table" },
          `<tr><th>Crash</th><th>Fund</th><th>Index</th></tr>` + R.stress.map(e => row(e.name, e.strategy, e.benchmark, v => pct(v, 1))).join("")));
      }
      if (R.split2020) {
        const b = R.split2020.before, a = R.split2020.after;
        f.appendChild(h("table", { class: "mini-table" },
          `<tr><th>Yearly return</th><th>Fund</th><th>Index</th></tr>` +
          row(b.label, b.strategy, b.benchmark, v => pct(v, 1)) + row(a.label, a.strategy, a.benchmark, v => pct(v, 1))));
      }
      return layout(text, finishFigure(f));
    }
  });

  /* 14. Limits and credits */
  slides.push({
    chapter: 4, title: "What we can't claim",
    render() {
      const s = h("section", { class: "slide solo" });
      s.appendChild(textCol({
        chapter: "Honest testing",
        h2: "What this can't tell you",
        p: [`<ul class="checklist">
          <li>Before April 2019 the data has no company sizes, so "mid" and "small" are defined by trading activity in the early years.</li>
          <li>We can't screen out shares under exchange surveillance, because that history isn't published in our data.</li>
          <li>There is no limit per industry, because most companies in the data have no industry label.</li>
          <li>In 2012–13 few small caps were liquid enough, so the early fund leaned towards mid caps.</li>
          <li>Five training years is short, and all results are simulations, not real trading.</li>
        </ul>`,
            `<b>${D.meta.authors.join(" and ")}</b>. ${D.meta.project}.` + (D.meta.repoUrl ? ` <a href="${D.meta.repoUrl}">Code and data on GitHub</a>.` : ""),
            `${D.meta.aiNote} This is an educational project, not investment advice.`]
      }));
      return s;
    }
  });

  /* ---------- navigation ---------- */
  const chapters = ["Basics", "The signal", "Building the fund", "Staying safe", "Honest testing"];
  const stage = document.getElementById("stage"), prev = document.getElementById("prev"), next = document.getElementById("next");
  const prog = document.getElementById("progress"), chap = document.getElementById("chapters");
  let cur = 0;

  chapters.forEach((name, ci) => {
    const b = h("button", { type: "button" }, name);
    b.addEventListener("click", () => go(slides.findIndex(s => s.chapter === ci && s !== slides[0]) || 0));
    chap.appendChild(b);
  });
  slides.forEach((s, i) => {
    const b = h("button", { type: "button", "aria-label": "Slide " + (i + 1) + ": " + s.title, title: s.title });
    b.addEventListener("click", () => go(i));
    prog.appendChild(b);
  });

  function go(i, fromHash) {
    cur = Math.max(0, Math.min(slides.length - 1, i));
    stage.innerHTML = "";
    stage.appendChild(slides[cur].render());
    prev.disabled = cur === 0;
    next.disabled = cur === slides.length - 1;
    next.innerHTML = cur === slides.length - 1 ? "End" : 'Next<span class="next-title">: ' + slides[cur + 1].title + "</span>";
    document.getElementById("count").textContent = (cur + 1) + " / " + slides.length;
    prog.querySelectorAll("button").forEach((b, k) => b.setAttribute("aria-current", String(k === cur)));
    chap.querySelectorAll("button").forEach((b, k) => b.setAttribute("aria-current", String(k === slides[cur].chapter && cur > 0)));
    if (!fromHash) history.replaceState(null, "", "#/" + cur);
    const t = stage.querySelector(".slide-title");
    if (t && cur > 0) t.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
  prev.addEventListener("click", () => go(cur - 1));
  next.addEventListener("click", () => go(cur + 1));
  document.addEventListener("keydown", e => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (e.key === "ArrowRight") go(cur + 1);
    if (e.key === "ArrowLeft") go(cur - 1);
  });
  let tx = null;
  stage.addEventListener("touchstart", e => { tx = e.target.closest("input, .figure") ? null : e.touches[0].clientX; }, { passive: true });
  stage.addEventListener("touchend", e => {
    if (tx === null) return;
    const dx = e.changedTouches[0].clientX - tx;
    if (Math.abs(dx) > 60) go(cur + (dx < 0 ? 1 : -1));
    tx = null;
  });
  window.addEventListener("hashchange", () => go(parseInt((location.hash.match(/\d+/) || ["0"])[0], 10), true));
  go(parseInt((location.hash.match(/\d+/) || ["0"])[0], 10), true);
})();
