(function () {
  "use strict";

  var project = document.body.dataset.project;
  document.querySelectorAll(
    ".ecosystem-rail__links [data-project], .ecosystem-grid [data-project]"
  ).forEach(function (link) {
    if (link.dataset.project === project) link.setAttribute("aria-current", "page");
  });

  var rail = document.querySelector(".ecosystem-rail");
  var projectToggle = rail && rail.querySelector(".ecosystem-rail__brand");
  var projectMenu = rail && rail.querySelector(".ecosystem-rail__links");
  function setProjectMenu(open) {
    if (!rail || !projectToggle || !projectMenu) return;
    rail.classList.toggle("is-open", open);
    projectToggle.setAttribute("aria-expanded", String(open));
    projectToggle.setAttribute("aria-label", (open ? "Close" : "Open") + " workshop projects");
    projectMenu.setAttribute("aria-hidden", String(!open));
    if (open) projectMenu.removeAttribute("inert");
    else projectMenu.setAttribute("inert", "");
  }
  if (projectToggle && projectMenu) {
    projectToggle.addEventListener("click", function () {
      setProjectMenu(projectToggle.getAttribute("aria-expanded") !== "true");
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && projectToggle.getAttribute("aria-expanded") === "true") {
        setProjectMenu(false);
        projectToggle.focus();
      }
    });
    document.addEventListener("click", function (event) {
      if (projectToggle.getAttribute("aria-expanded") === "true" && !rail.contains(event.target)) {
        setProjectMenu(false);
      }
    });
  }

  var progress = document.querySelector(".ecosystem-progress span");
  var ticking = false;
  var compact = false;
  function updateProgress() {
    var nextCompact = compact ? scrollY > 24 : scrollY > 72;
    if (nextCompact !== compact) {
      compact = nextCompact;
      document.documentElement.classList.toggle("ecosystem-compact", compact);
    }
    if (progress) {
      var available = document.documentElement.scrollHeight - innerHeight;
      var amount = available > 0 ? Math.min(1, scrollY / available) : 0;
      progress.style.transform = "scaleX(" + amount + ")";
    }
    ticking = false;
  }
  addEventListener("scroll", function () {
    if (!ticking) {
      requestAnimationFrame(updateProgress);
      ticking = true;
    }
  }, { passive: true });
  updateProgress();

  var reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduceMotion && "IntersectionObserver" in window) {
    document.documentElement.classList.add("ecosystem-ready");
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });
    document.querySelectorAll("[data-eco-reveal]").forEach(function (el) {
      observer.observe(el);
    });
  }

  document.querySelectorAll(".ecosystem-card").forEach(function (card) {
    card.addEventListener("pointermove", function (event) {
      var box = card.getBoundingClientRect();
      card.style.setProperty("--mx", (event.clientX - box.left) + "px");
      card.style.setProperty("--my", (event.clientY - box.top) + "px");
    });
  });

  /* The SVG geometry never moves. Asymmetric, portrait-derived light fields
     turn behind its mask, so colour exists only in the linework while the page
     itself remains the background. */
  var mark = document.querySelector(".ecosystem-mark");
  var colourLayer = mark && mark.querySelector(".ecosystem-mark__colour");
  var lightField = mark && mark.querySelector(".ecosystem-mark__light-field");
  if (!mark || !colourLayer || !lightField) return;

  var markPalettesDark = [
    ["#060919", "#eae9ff", "#5d72d8", "#9183d8", "#cfd7ff"], // wolf
    ["#07150f", "#0c5b43", "#d1a44a", "#f6e2b1", "#344968"], // dragon
    ["#071c19", "#175241", "#7a182a", "#dc5c32", "#e8b777"], // deer
    ["#120b17", "#e05b24", "#24cad4", "#e82d9d", "#7360c5"], // panda
    ["#101a37", "#cce91d", "#c5c9d1", "#3855a6", "#f0efe6"], // rat
    ["#23130d", "#7b4026", "#ef7865", "#2782a3", "#e7c095"], // otter
    ["#0b0710", "#eee9df", "#474750", "#7e1735", "#d32656"]  // leopard / void
  ];
  /* On a light surface the near-black anchors above occupy too much of the
     tiny mark and read as holes. These keep the same identities, but lift the
     broad base field into saturated midtones and reserve true black for the
     three-second reset between palettes. */
  var markPalettesLight = [
    ["#6079dc", "#e8eaff", "#4665d1", "#8b70d1", "#b6c7ff"], // wolf
    ["#28725c", "#168463", "#c79222", "#f0c85f", "#486e91"], // dragon
    ["#277766", "#238368", "#a52d43", "#e7592f", "#d8a13c"], // deer
    ["#7540a0", "#eb6428", "#08aebc", "#d9298f", "#6750bd"], // panda
    ["#536fbd", "#9fbd00", "#9ca4b8", "#3159b1", "#ded76d"], // rat
    ["#a35c3d", "#97502e", "#e86257", "#16829d", "#d29b4e"], // otter
    ["#81405c", "#d8cad5", "#686574", "#9f2149", "#d62958"]  // leopard / void
  ];
  var forcedTheme = document.documentElement.dataset.theme;
  var markOnLight = forcedTheme === "light" ||
    (forcedTheme !== "dark" && matchMedia("(prefers-color-scheme: light)").matches);
  var markPalettes = markOnLight ? markPalettesLight : markPalettesDark;
  var markReduced = matchMedia("(prefers-reduced-motion: reduce)");
  var markVisible = true;
  var markFrame = 0;
  var markStarted = performance.now();
  var markPalette = -1;
  var markColourTime = 3000;
  var markResetTime = 3000;
  var markSegment = markColourTime + markResetTime;
  var markCycle = markSegment * markPalettes.length;
  var markTurn = markCycle * 2;

  function markEase(value) {
    return value * value * (3 - 2 * value);
  }
  function setMarkPalette(index) {
    if (index === markPalette) return;
    markPalette = index;
    mark.style.setProperty("--eco-mark-c0", markPalettes[index][0]);
    mark.style.setProperty("--eco-mark-c1", markPalettes[index][1]);
    mark.style.setProperty("--eco-mark-c2", markPalettes[index][2]);
    mark.style.setProperty("--eco-mark-c3", markPalettes[index][3]);
    mark.style.setProperty("--eco-mark-c4", markPalettes[index][4]);
  }
  function paintMark(now) {
    var elapsed = (now - markStarted) % markCycle;
    var position = elapsed % markSegment;
    var index = Math.floor(elapsed / markSegment);
    var opacity;

    setMarkPalette(index);
    if (position < 500) opacity = markEase(position / 500);
    else if (position < 2500) opacity = 1;
    else if (position < markColourTime) opacity = 1 - markEase((position - 2500) / 500);
    else opacity = 0;

    colourLayer.style.opacity = opacity.toFixed(3);
    lightField.setAttribute("transform", "rotate(" + (((now - markStarted) % markTurn) / markTurn * 360).toFixed(3) + ")");
  }
  function tickMark(now) {
    if (!markVisible || document.hidden) { markFrame = 0; return; }
    paintMark(now);
    if (!markReduced.matches) markFrame = requestAnimationFrame(tickMark);
    else markFrame = 0;
  }
  function startMark() {
    if (markReduced.matches) {
      colourLayer.style.opacity = "0";
      lightField.setAttribute("transform", "rotate(0)");
      markFrame = 0;
    } else if (!markFrame) {
      markFrame = requestAnimationFrame(tickMark);
    }
  }
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      markVisible = entries[0].isIntersecting;
      if (markVisible) startMark();
    }).observe(mark);
  }
  addEventListener("visibilitychange", function () {
    if (!document.hidden) startMark();
  });
  if (markReduced.addEventListener) markReduced.addEventListener("change", startMark);
  else markReduced.addListener(startMark);
  startMark();
})();
