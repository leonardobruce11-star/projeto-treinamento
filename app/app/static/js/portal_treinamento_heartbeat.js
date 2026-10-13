(() => {
    "use strict";

    const previous = window.oneTreinamentoHeartbeat;
    if (previous) previous.destroy();

    const config = document.querySelector("script[data-one-heartbeat-url]");
    if (!config || !config.dataset.oneHeartbeatUrl || !config.dataset.csrfToken ||
        typeof window.fetch !== "function") return;

    const INTERVAL_MS = 15000;
    // Após 60 segundos sem interação, interrompe os heartbeats.
    const IDLE_MS = 60000;
    const events = ["mousemove", "mousedown", "keydown", "touchstart", "scroll"];
    let lastActivity = performance.now();
    let timer = null;
    let pending = false;
    let destroyed = false;
    const controller = new AbortController();

    function active() {
        return !destroyed && !document.hidden && performance.now() - lastActivity < IDLE_MS;
    }

    function stop() {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
    }

    function schedule() {
        if (timer === null && active()) timer = window.setTimeout(tick, INTERVAL_MS);
    }

    async function tick() {
        timer = null;
        if (!active()) return;
        if (!pending) {
            pending = true;
            // Falhas HTTP/rede são silenciosas; nunca bloqueiam o formulário.
            try {
                await window.fetch(config.dataset.oneHeartbeatUrl, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": config.dataset.csrfToken,
                    },
                    body: JSON.stringify({ segundos: 15 }),
                    signal: controller.signal,
                });
            } catch (_) {
                // Sem mensagens técnicas, retries imediatos ou compensação.
            } finally {
                pending = false;
                schedule();
            }
        }
    }

    function activity() {
        if (destroyed || document.hidden) return;
        lastActivity = performance.now();
        schedule();
    }

    function visibility() {
        stop();
        if (!document.hidden) activity();
    }

    function pagehide() {
        stop();
    }

    function pageshow() {
        visibility();
    }

    events.forEach(event => document.addEventListener(event, activity, { passive: true }));
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", pagehide);
    window.addEventListener("pageshow", pageshow);
    window.oneTreinamentoHeartbeat = {
        destroy() {
            destroyed = true;
            stop();
            controller.abort();
            events.forEach(event => document.removeEventListener(event, activity));
            document.removeEventListener("visibilitychange", visibility);
            window.removeEventListener("pagehide", pagehide);
            window.removeEventListener("pageshow", pageshow);
            delete window.oneTreinamentoHeartbeat;
        },
    };
    schedule();
})();
