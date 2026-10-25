/* 기사 맨 아래 '읽음' 체크 칸 (2026-10-25 사용자 결정).
   체크한 기사는 이 브라우저에만 기억한다(localStorage, 다른 기기와 공유되지 않는다).
   홈(main[data-hide-read])에서는 체크한 기사를 숨기고, 기사가 모두 숨은 분야와 주요 뉴스 줄도 숨긴다.
   맨 아래 버튼으로 숨긴 기사를 다시 보거나 체크를 풀 수 있다. 날짜별 브리핑과 기사 페이지는 숨기지 않는다. */
(function () {
  var KEY = "dogcowai-read";
  var KEEP_MS = 120 * 24 * 3600 * 1000;
  var boxes = document.querySelectorAll("input[data-read]");
  if (!boxes.length) return;

  var store = {};
  try { store = JSON.parse(window.localStorage.getItem(KEY) || "{}") || {}; } catch (e) { store = {}; }
  var now = Date.now();
  Object.keys(store).forEach(function (k) { if (!(now - store[k] < KEEP_MS)) delete store[k]; });
  function save() {
    try { window.localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) { /* 저장이 막힌 브라우저 */ }
  }

  var main = document.querySelector("main[data-hide-read]");
  var restore = null;

  function refresh() {
    if (!main) return;
    var hidden = 0;
    Array.prototype.forEach.call(boxes, function (box) {
      var art = box.closest("article");
      var read = box.checked;
      art.classList.toggle("is-read", read);
      if (read) hidden++;
    });
    Array.prototype.forEach.call(main.querySelectorAll("section.sec"), function (sec) {
      sec.classList.toggle("is-read", !sec.querySelector("article:not(.is-read)"));
    });
    var quick = main.querySelector("section.quick");
    if (quick) {
      Array.prototype.forEach.call(quick.querySelectorAll("li"), function (li) {
        var a = li.querySelector("a[href^='#']");
        var art = a && document.getElementById(a.getAttribute("href").slice(1));
        li.classList.toggle("is-read", !!(art && art.classList.contains("is-read")));
      });
      quick.classList.toggle("is-read", !quick.querySelector("li:not(.is-read)"));
    }
    if (!restore) {
      restore = document.createElement("p");
      restore.className = "read-restore";
      restore.innerHTML = '<button type="button"></button>';
      main.querySelector(".body").appendChild(restore);
      restore.firstChild.addEventListener("click", function () {
        main.classList.toggle("show-read");
        refresh();
      });
    }
    restore.hidden = hidden === 0;
    restore.firstChild.textContent = main.classList.contains("show-read")
      ? "읽은 기사 다시 숨기기" : "읽은 기사 " + hidden + "건 다시 보기";
  }

  Array.prototype.forEach.call(boxes, function (box) {
    var key = box.getAttribute("data-read");
    box.checked = Object.prototype.hasOwnProperty.call(store, key);
    box.closest(".readmark").hidden = false;
    box.addEventListener("change", function () {
      if (box.checked) store[key] = Date.now(); else delete store[key];
      save();
      refresh();
    });
  });
  refresh();
})();
