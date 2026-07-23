const tabs = document.querySelectorAll("[data-tab]");
const panels = document.querySelectorAll("[data-panel]");

for (const tab of tabs) {
  tab.addEventListener("click", () => {
    for (const item of tabs) {
      const selected = item === tab;
      item.classList.toggle("active", selected);
      item.setAttribute("aria-selected", String(selected));
    }
    for (const panel of panels) panel.classList.toggle("active", panel.dataset.panel === tab.dataset.tab);
    const toolbar = document.querySelector(".code-toolbar span");
    toolbar.textContent = tab.dataset.tab === "agent" ? "AGENT PLAYBOOK" : "POST /api/v1/submissions";
    document.querySelector(".copy").dataset.copyTarget = document.querySelector(".code-panel.active").id || "";
  });
}

document.querySelector(".copy")?.addEventListener("click", async (event) => {
  const active = document.querySelector(".code-panel.active");
  await navigator.clipboard.writeText(active?.innerText || "");
  event.currentTarget.textContent = "Copied";
  setTimeout(() => { event.currentTarget.textContent = "Copy"; }, 1200);
});

document.querySelector("#status-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = new FormData(event.currentTarget).get("id").trim();
  const output = document.querySelector("#status-output");
  output.replaceChildren(document.createElement("p"));
  output.firstChild.textContent = "Loading public record…";
  try {
    const response = await fetch(`/api/v1/submissions/${encodeURIComponent(id)}`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || "Could not load submission.");
    const heading = document.createElement("p");
    const pill = document.createElement("span");
    pill.className = "status-pill";
    pill.textContent = body.status;
    heading.append(pill, `  ${body.model.name} · system version ${body.model.version}`);
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(body, null, 2);
    const axes = document.createElement("dl");
    axes.className = "result-axes";
    const scores = body.result?.scores;
    if (scores) {
      for (const [label, value] of [
        ["Leaderboard class", scores.leaderboard_class],
        ["Eligible", scores.leaderboard_eligible],
        ["Accounting complete", scores.accounting_complete],
        ["Acquisition", scores.acquisition_rate],
        ["Observed coverage", scores.observed_coverage],
        ["CPU seconds", scores.cpu_time_seconds],
        ["CPU s/source s", scores.cpu_seconds_per_native_source_second],
        ["Real-time factor", scores.real_time_factor],
        ["Teardown seconds", scores.teardown_seconds],
        ["Peak RAM bytes", scores.peak_ram_bytes],
        ["Replay", scores.replay_profile != null && scores.replay_rate != null
          ? `${scores.replay_profile} @ ${scores.replay_rate}x`
          : null],
      ]) {
        const term = document.createElement("dt");
        term.textContent = label;
        const detail = document.createElement("dd");
        detail.textContent = value ?? "unavailable";
        axes.append(term, detail);
      }
    }
    output.replaceChildren(heading, ...(scores ? [axes] : []), pre);
    history.replaceState(null, "", `#results?submission=${id}`);
  } catch (error) {
    output.textContent = error.message;
  }
});

const queryId = new URLSearchParams(location.hash.split("?")[1] || "").get("submission");
if (queryId) {
  document.querySelector("#submission-id").value = queryId;
  document.querySelector("#status-form").requestSubmit();
}
