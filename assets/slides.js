/* 주요 뉴스 상세와 분야별 기사: 기사 카드를 한 장씩 옆으로 넘긴다 (2026-10-25 사용자 결정).
   넘김 자체는 CSS(scroll-snap)가 한다. 이 파일은 이전·다음 단추, 'n / 전체' 표시,
   그리고 칸 높이를 지금 보이는 카드에 맞추는 일만 한다. 스크립트가 없어도 손으로 밀어 볼 수 있다. */
(function () {
function setup(box) {
  var track = box.querySelector(".slides-track");
  var nav = box.querySelector(".slides-nav");
  var count = nav.querySelector(".slides-count");
  var prev = nav.querySelector(".prev");
  var next = nav.querySelector(".next");

  function cards() {
    return Array.prototype.filter.call(track.querySelectorAll(".slide"), function (c) {
      return c.offsetParent !== null;
    });
  }
  function current(list) {
    var best = 0, gap = Infinity;
    list.forEach(function (c, n) {
      var d = Math.abs(c.offsetLeft - track.scrollLeft);
      if (d < gap) { gap = d; best = n; }
    });
    return best;
  }
  function update() {
    var list = cards();
    nav.hidden = list.length < 2;
    if (!list.length) { track.style.height = ""; return; }
    var n = current(list);
    count.textContent = (n + 1) + " / " + list.length;
    prev.disabled = n === 0;
    next.disabled = n === list.length - 1;
    track.style.height = list[n].offsetHeight + "px";
  }
  function go(step) {
    var list = cards();
    var n = Math.min(Math.max(current(list) + step, 0), list.length - 1);
    track.scrollTo({ left: list[n].offsetLeft, behavior: "smooth" });
  }
  prev.addEventListener("click", function () { go(-1); });
  next.addEventListener("click", function () { go(1); });

  var timer = null;
  track.addEventListener("scroll", function () {
    clearTimeout(timer);
    timer = setTimeout(update, 80);
  }, { passive: true });
  window.addEventListener("resize", update);
  window.addEventListener("hashchange", function () { setTimeout(update, 120); });
  document.addEventListener("dogcowai:read", update);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(update);
  if (window.ResizeObserver) {
    var watch = new ResizeObserver(function () { clearTimeout(timer); timer = setTimeout(update, 80); });
    Array.prototype.forEach.call(track.querySelectorAll(".slide"), function (c) { watch.observe(c); });
  }
  update();
}
Array.prototype.forEach.call(document.querySelectorAll("section.slides"), setup);
})();
