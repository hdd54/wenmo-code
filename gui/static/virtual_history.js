(function () {
  "use strict";

  var MAX_DOM_MESSAGES = 180;
  var PAGE_MESSAGES = 60;
  var container = null;
  var renderRange = null;
  var total = 0;
  var start = 0;
  var end = 0;
  var averageHeight = 96;
  var topSpacer = null;
  var bottomSpacer = null;
  var rendering = false;

  function makeSpacer(className) {
    var spacer = document.createElement("div");
    spacer.className = "virtual-spacer " + className;
    spacer.setAttribute("aria-hidden", "true");
    return spacer;
  }

  function removeVirtualChildren() {
    if (!container) return;
    Array.prototype.slice.call(container.children).forEach(function (child) {
      if (child.classList.contains("msg") || child.classList.contains("virtual-spacer")) {
        container.removeChild(child);
      }
    });
  }

  function updateSpacers() {
    if (!topSpacer || !bottomSpacer) return;
    topSpacer.style.height = Math.max(0, start * averageHeight) + "px";
    bottomSpacer.style.height = Math.max(0, (total - end) * averageHeight) + "px";
  }

  function measure() {
    if (!container) return;
    var rows = container.querySelectorAll(".msg[data-idx]");
    if (!rows.length) return;
    var sum = 0;
    rows.forEach(function (row) { sum += Math.max(24, row.getBoundingClientRect().height); });
    averageHeight = Math.max(48, Math.min(800, sum / rows.length));
    updateSpacers();
  }

  function draw(nextStart, nextEnd, preserveIndex, preserveOffset) {
    if (!container || !renderRange || rendering) return;
    rendering = true;
    start = Math.max(0, Math.min(nextStart, total));
    end = Math.max(start, Math.min(nextEnd, total));
    removeVirtualChildren();
    topSpacer = makeSpacer("virtual-spacer-top");
    bottomSpacer = makeSpacer("virtual-spacer-bottom");
    container.appendChild(topSpacer);
    var fragment = document.createDocumentFragment();
    renderRange(fragment, start, end);
    container.appendChild(fragment);
    container.appendChild(bottomSpacer);
    updateSpacers();
    measure();
    if (typeof preserveIndex === "number") {
      var anchor = container.querySelector('.msg[data-idx="' + preserveIndex + '"]');
      if (anchor) container.scrollTop += anchor.getBoundingClientRect().top - preserveOffset;
    }
    rendering = false;
  }

  function firstVisible() {
    var top = container.getBoundingClientRect().top;
    var rows = container.querySelectorAll(".msg[data-idx]");
    for (var i = 0; i < rows.length; i++) {
      var rect = rows[i].getBoundingClientRect();
      if (rect.bottom >= top) return { index: Number(rows[i].dataset.idx), offset: rect.top };
    }
    return null;
  }

  function onScroll() {
    if (!container || rendering || container.querySelector(".msg-ai:not(.saved)")) return;
    if (container.scrollTop < 320 && start > 0) {
      var anchor = firstVisible();
      var nextStart = Math.max(0, start - PAGE_MESSAGES);
      draw(nextStart, Math.min(total, nextStart + MAX_DOM_MESSAGES),
        anchor && anchor.index, anchor && anchor.offset);
    } else if (container.scrollHeight - container.scrollTop - container.clientHeight < 320 && end < total) {
      var nextEnd = Math.min(total, end + PAGE_MESSAGES);
      draw(Math.max(0, nextEnd - MAX_DOM_MESSAGES), nextEnd);
    }
  }

  function bind(nextContainer) {
    if (container === nextContainer) return;
    if (container) container.removeEventListener("scroll", onScroll);
    container = nextContainer;
    if (container) container.addEventListener("scroll", onScroll, { passive: true });
  }

  function render(options) {
    bind(options.container);
    renderRange = options.renderRange;
    total = Math.max(0, Number(options.total) || 0);
    var latestEnd = total;
    draw(Math.max(0, latestEnd - MAX_DOM_MESSAGES), latestEnd);
    if (options.forceBottom && container) container.scrollTop = container.scrollHeight;
  }

  function ensureIndex(index) {
    index = Math.max(0, Math.min(Number(index) || 0, Math.max(0, total - 1)));
    if (index < start || index >= end) {
      var half = Math.floor(MAX_DOM_MESSAGES / 2);
      var nextStart = Math.max(0, Math.min(index - half, total - MAX_DOM_MESSAGES));
      draw(nextStart, Math.min(total, nextStart + MAX_DOM_MESSAGES));
    }
  }

  function reset() {
    removeVirtualChildren();
    total = start = end = 0;
    topSpacer = bottomSpacer = null;
  }

  window.WenmoVirtualHistory = {
    MAX_DOM_MESSAGES: MAX_DOM_MESSAGES,
    render: render,
    ensureIndex: ensureIndex,
    reset: reset
  };
})();
