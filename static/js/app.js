
document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function () {
            const button = form.querySelector('button[type="submit"]');
            if (button && !button.disabled) {
                button.disabled = true;
                button.dataset.originalText = button.textContent;
                button.textContent = "Processing...";
            }
        });
    });

    const refreshButton = document.getElementById("refresh-dashboard");
    if (refreshButton) {
        refreshButton.addEventListener("click", function () {
            refreshButton.disabled = true;
            refreshButton.textContent = "Refreshing...";
            window.location.reload();
        });
    }

    const clock = document.getElementById("local-clock");
    function updateClock() {
        if (clock) clock.textContent = new Date().toLocaleTimeString();
    }
    updateClock();
    window.setInterval(updateClock, 1000);
});
