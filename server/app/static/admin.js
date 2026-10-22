/* NexHub 管理后台轻量交互 */
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form[data-confirm]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            const message = form.dataset.confirm || "确定执行该操作？";
            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    document.querySelectorAll(".flash").forEach((flash) => {
        window.setTimeout(() => {
            flash.style.transition = "opacity .4s ease";
            flash.style.opacity = "0";
            window.setTimeout(() => flash.remove(), 450);
        }, 4500);
    });

    document.querySelectorAll(".action-details").forEach((details) => {
        document.addEventListener("click", (event) => {
            if (!details.contains(event.target)) {
                details.removeAttribute("open");
            }
        });
    });

    const active = document.querySelector(".nav a.active");
    if (active) {
        active.scrollIntoView({ block: "nearest" });
    }
});
