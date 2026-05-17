document.addEventListener("DOMContentLoaded", function () {
  var audio = null;

  function hijackLogo() {
    var el = document.querySelector(".md-header__topic .md-ellipsis");
    if (!el || el.dataset.egg) return;
    el.dataset.egg = "1";

    var text = el.textContent;
    var idx = text.lastIndexOf("e");
    if (idx === -1) return;

    var before = text.slice(0, idx);
    var after = text.slice(idx + 1);

    el.textContent = "";
    el.appendChild(document.createTextNode(before));

    var span = document.createElement("span");
    span.textContent = "e";
    span.style.cursor = "pointer";
    span.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (audio && !audio.paused) {
        audio.pause();
        audio.currentTime = 0;
        return;
      }
      var base = document.querySelector('link[rel="canonical"]');
      var root = base ? new URL(".", base.href).href : "/";
      audio = new Audio(root + "assets/egg.mp3");
      audio.play();
    });
    el.appendChild(span);
    el.appendChild(document.createTextNode(after));
  }

  hijackLogo();
  new MutationObserver(hijackLogo).observe(document.body, {
    childList: true,
    subtree: true,
  });
});
