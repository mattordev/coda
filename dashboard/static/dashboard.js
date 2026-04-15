const state = {
	lastAiEventCounter: null,
	resetTimer: null,
	autoRefreshTimer: null,
	latestMessages: [],
};

const REFRESH_INTERVAL_MS = 30000;
const HEARTBEAT_STALE_MS = 6000;

function formatTimestamp(timestamp) {
	if (!timestamp) {
		return "Just now";
	}

	const date = new Date(timestamp * 1000);
	return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function renderTranscript(messages) {
	const transcriptBody = document.getElementById("transcript-body");
	const filterInput = document.getElementById("transcript-filter");
	const filterValue = (filterInput?.value || "").trim().toLowerCase();

	if (!transcriptBody) {
		return;
	}

	if (!messages || messages.length === 0) {
		transcriptBody.innerHTML = '<div class="empty-state">No speech captured yet.</div>';
		return;
	}

	const filteredMessages = messages.filter((message) => {
		if (!filterValue) {
			return true;
		}

		const source = String(message.source || "").toLowerCase();
		const text = String(message.text || "").toLowerCase();
		const tags = Array.isArray(message.tags) ? message.tags.join(" ").toLowerCase() : "";
		return source.includes(filterValue) || text.includes(filterValue) || tags.includes(filterValue);
	});

	if (filteredMessages.length === 0) {
		transcriptBody.innerHTML = '<div class="empty-state">No transcript entries match this filter.</div>';
		return;
	}

	transcriptBody.innerHTML = filteredMessages
		.slice()
		.reverse()
		.map(
			(message) => `
            <article class="transcript-entry">
                <small>${escapeHtml(message.source || "voice")} · ${formatTimestamp(message.timestamp)}</small>
                <div>${escapeHtml(String(message.text || ""))}</div>
                ${renderTags(message.tags)}
            </article>
        `,
		)
		.join("");
}

function renderTags(tags) {
	if (!Array.isArray(tags) || tags.length === 0) {
		return "";
	}

	const tagMarkup = tags.map((tag) => `<span class="event-tag">${escapeHtml(formatTag(tag))}</span>`).join("");

	return `<div class="event-tags">${tagMarkup}</div>`;
}

function formatTag(tag) {
	return String(tag || "")
		.replaceAll("_", " ")
		.trim();
}

function updateStatus(statePayload) {
	const lastUser = document.getElementById("last-user-text");
	const lastAi = document.getElementById("last-ai-text");
	const eventCounter = document.getElementById("event-counter");
	const lastUpdated = document.getElementById("last-updated");

	if (lastUser) {
		lastUser.textContent = statePayload.last_user_message || "Waiting for speech...";
	}

	if (lastAi) {
		lastAi.textContent = statePayload.last_ai_message || "No response yet";
	}

	if (eventCounter) {
		const count = statePayload.event_counter || 0;
		eventCounter.textContent = `${count} event${count === 1 ? "" : "s"}`;
	}

	if (lastUpdated) {
		lastUpdated.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
	}
}

function setConnectionStatus(status) {
	const connectionPill = document.getElementById("connection-pill");
	const connectionText = document.getElementById("connection-text");

	if (!connectionPill || !connectionText) {
		return;
	}

	if (status === "ok") {
		connectionPill.dataset.state = "ok";
		connectionText.textContent = "Live connection";
		return;
	}

	if (status === "error") {
		connectionPill.dataset.state = "error";
		connectionText.textContent = "Connection issue";
		return;
	}

	if (status === "stale") {
		connectionPill.dataset.state = "stale";
		connectionText.textContent = "Main process idle/offline";
		return;
	}

	connectionPill.dataset.state = "connecting";
	connectionText.textContent = "Connecting...";
}

function moveBubble(motion) {
	const bubble = document.getElementById("ai-bubble");
	if (!bubble) {
		return;
	}

	const x = motion?.x ?? 0;
	const y = motion?.y ?? 0;
	const scale = motion?.scale ?? 1;
	const rotation = motion?.rotation ?? 0;

	bubble.classList.add("bubble--active");
	bubble.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${scale}) rotate(${rotation}deg)`;

	if (state.resetTimer) {
		window.clearTimeout(state.resetTimer);
	}

	state.resetTimer = window.setTimeout(() => {
		bubble.style.transform = "translate3d(0, 0, 0) scale(1) rotate(0deg)";
		bubble.classList.remove("bubble--active");
	}, 1100);
}

function escapeHtml(value) {
	return value
		.replaceAll("&", "&amp;")
		.replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;")
		.replaceAll('"', "&quot;")
		.replaceAll("'", "&#039;");
}

async function refreshDashboard() {
	setConnectionStatus("connecting");

	try {
		const response = await fetch("/api/state", { cache: "no-store" });
		if (!response.ok) {
			setConnectionStatus("error");
			return;
		}

		const payload = await response.json();
		state.latestMessages = payload.user_messages || [];
		updateStatus(payload);
		renderTranscript(state.latestMessages);

		const heartbeatAt = Number(payload.heartbeat_at || 0);
		const heartbeatAgeMs = heartbeatAt > 0 ? Date.now() - heartbeatAt * 1000 : Number.POSITIVE_INFINITY;
		setConnectionStatus(heartbeatAgeMs <= HEARTBEAT_STALE_MS ? "ok" : "stale");

		if (payload.ai_event_counter !== state.lastAiEventCounter) {
			moveBubble(payload.last_ai_motion);
			state.lastAiEventCounter = payload.ai_event_counter;
		}
	} catch (error) {
		setConnectionStatus("error");
		console.error("Unable to refresh dashboard state:", error);
	}
}

function setAutoRefresh(enabled) {
	if (state.autoRefreshTimer) {
		window.clearInterval(state.autoRefreshTimer);
		state.autoRefreshTimer = null;
	}

	if (enabled) {
		state.autoRefreshTimer = window.setInterval(refreshDashboard, REFRESH_INTERVAL_MS);
	}
}

function initDashboardControls() {
	const refreshButton = document.getElementById("refresh-button");
	const autoRefreshToggle = document.getElementById("autorefresh-toggle");
	const filterInput = document.getElementById("transcript-filter");

	if (refreshButton) {
		refreshButton.addEventListener("click", () => {
			refreshDashboard();
		});
	}

	if (autoRefreshToggle) {
		autoRefreshToggle.addEventListener("change", (event) => {
			setAutoRefresh(Boolean(event.target?.checked));
		});
	}

	if (filterInput) {
		filterInput.addEventListener("input", () => {
			renderTranscript(state.latestMessages);
		});
	}
}

initDashboardControls();
refreshDashboard();
setAutoRefresh(true);
