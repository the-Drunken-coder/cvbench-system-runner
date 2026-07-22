let operatorToken = "";
let selectedJobId = "";
let timer;

document.querySelector("#operator-auth")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  operatorToken = document.querySelector("#operator-token").value;
  document.querySelector("#operator-console").hidden = false;
  await refreshJobs();
  scheduleRefresh();
});
document.querySelector("#refresh")?.addEventListener("click", refreshJobs);
document.querySelector("#status-filter")?.addEventListener("change", refreshJobs);
document.querySelector("#auto-refresh")?.addEventListener("change", scheduleRefresh);

async function refreshJobs() {
  if (!operatorToken) return;
  const status = document.querySelector("#status-filter").value;
  const response = await fetch(`/api/v1/operator/jobs${status ? `?status=${encodeURIComponent(status)}` : ""}`, { headers: { authorization: `Bearer ${operatorToken}` } });
  if (!response.ok) return showMessage(`Operator API returned ${response.status}.`);
  const body = await response.json();
  const list = document.querySelector("#job-list");
  list.replaceChildren();
  for (const job of body.jobs) {
    const button = document.createElement("button");
    button.className = `job-row${job.id === selectedJobId ? " selected" : ""}`;
    button.type = "button";
    button.addEventListener("click", () => selectJob(job.id));
    const title = document.createElement("strong");
    title.textContent = `${job.model.name} · ${job.model.version}`;
    const meta = document.createElement("span");
    meta.textContent = `${job.status} · attempt ${job.queue.attempt} · ${job.id}`;
    button.append(title, meta);
    list.append(button);
  }
  if (selectedJobId) await selectJob(selectedJobId);
}

async function selectJob(id) {
  selectedJobId = id;
  const response = await fetch(`/api/v1/operator/jobs/${encodeURIComponent(id)}`, { headers: { authorization: `Bearer ${operatorToken}` } });
  const detail = document.querySelector("#job-detail");
  if (!response.ok) return showMessage(`Could not load job (${response.status}).`);
  const body = await response.json();
  const title = document.createElement("h3");
  title.textContent = `${body.job.model.name} · ${body.job.status}`;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(body, null, 2);
  detail.replaceChildren(title, pre);
}

function scheduleRefresh() {
  clearTimeout(timer);
  if (document.querySelector("#auto-refresh")?.checked && operatorToken) timer = setTimeout(async () => { await refreshJobs(); scheduleRefresh(); }, 5000);
}

function showMessage(message) {
  const detail = document.querySelector("#job-detail");
  detail.textContent = message;
}
