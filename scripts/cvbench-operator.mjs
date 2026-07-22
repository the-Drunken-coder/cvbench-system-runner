#!/usr/bin/env node

const [command = "list", value] = process.argv.slice(2);
const baseUrl = required("CVBENCH_API_BASE_URL").replace(/\/$/, "");
const token = required("CVBENCH_OPERATOR_TOKEN");
let lastBody;

if (command === "watch") {
  const intervalMs = Math.max(1000, Number(process.env.CVBENCH_POLL_MS || 5000));
  do {
    await call(`/api/v1/operator/jobs${value ? `/${encodeURIComponent(value)}` : ""}`);
    if (value && terminal(lastBody?.job?.status)) break;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  } while (true);
} else if (["list", "detail", "audit", "evidence", "notes"].includes(command)) {
  const path = command === "list" ? "/api/v1/operator/jobs" : `/api/v1/operator/jobs/${encodeURIComponent(requiredArg(value, "job id"))}/${command === "detail" ? "" : command}`;
  await call(path.replace(/\/$/, ""));
} else {
  throw new Error("Usage: cvbench-operator.mjs list | watch [job-id] | detail <job-id> | audit <job-id> | evidence <job-id> | notes <job-id>");
}

async function call(path) {
  const response = await fetch(`${baseUrl}${path}`, { headers: { authorization: `Bearer ${token}`, accept: "application/json" } });
  const text = await response.text();
  try {
    lastBody = JSON.parse(text);
  } catch {
    throw new Error(`operator API returned non-JSON (${response.status})`);
  }
  if (!response.ok) throw new Error(`${response.status}: ${lastBody.error?.message || "operator API request failed"}`);
  process.stdout.write(`${JSON.stringify(lastBody, null, 2)}\n`);
}

function terminal(status) {
  return status === "succeeded" || status === "failed";
}

function required(name) {
  const result = process.env[name];
  if (!result) throw new Error(`${name} is required`);
  return result;
}

function requiredArg(result, label) {
  if (!result) throw new Error(`${label} is required`);
  return result;
}
