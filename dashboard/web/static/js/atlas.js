// Atlas UI — htmx + SSE scaffolding
document.addEventListener("DOMContentLoaded", function () {
    const dot = document.querySelector(".conn-dot");

    // SSE connection placeholder — wired when /events endpoint lands (M2)
    if (typeof EventSource !== "undefined" && dot) {
        const src = new EventSource("/events");
        src.onopen = () => {
            dot.classList.add("connected");
            dot.setAttribute("aria-label", "SSE connected");
            dot.setAttribute("title", "SSE connected");
        };
        src.onerror = () => {
            dot.classList.remove("connected");
            dot.setAttribute("aria-label", "SSE disconnected");
            dot.setAttribute("title", "SSE disconnected");
        };
    }

    // Keyboard accessibility: Enter / Space on any element with tabindex="0"
    // and a role of "link" or "button" should activate it like a click. This
    // covers <tr tabindex="0" role="link"> rows and any future card/row that
    // is wired for click-to-navigate via htmx.
    document.addEventListener("keydown", function (ev) {
        const t = ev.target;
        if (!(t instanceof Element)) return;
        if (t.getAttribute("tabindex") !== "0") return;
        const role = t.getAttribute("role");
        if (role !== "link" && role !== "button") return;
        if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
            ev.preventDefault();
            t.click();
        }
    });
});
