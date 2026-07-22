import assert from "node:assert/strict";
import { test } from "node:test";

import { createLatestScenarioLoader, exactFrameFailureMessage } from "../public/scenario-loader.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("a delayed detail request cannot overwrite the current history selection", async () => {
  const loader = createLatestScenarioLoader();
  const first = deferred();
  const second = deferred();
  let url = "/scenarios/?scenario=synthetic-acquisition";
  let view = null;
  const selectedId = () => new URL(url, "https://cvbench.example").searchParams.get("scenario");
  const commit = async (bundle) => { view = bundle; };

  const firstLoad = loader.load("synthetic-acquisition", () => first.promise, selectedId, commit);
  url = "/scenarios/?scenario=rv1-b2c8";
  const secondLoad = loader.load("rv1-b2c8", () => second.promise, selectedId, commit);
  second.resolve({ detail: { id: "rv1-b2c8" }, frames: { scenario_id: "rv1-b2c8" }, annotations: { scenario_id: "rv1-b2c8" } });
  assert.equal(await secondLoad, true);
  first.resolve({ detail: { id: "synthetic-acquisition" }, frames: { scenario_id: "synthetic-acquisition" }, annotations: { scenario_id: "synthetic-acquisition" } });
  assert.equal(await firstLoad, false);

  assert.equal(selectedId(), "rv1-b2c8");
  assert.equal(view.detail.id, "rv1-b2c8");
  assert.equal(view.frames.scenario_id, "rv1-b2c8");
  assert.equal(view.annotations.scenario_id, "rv1-b2c8");
});

test("missing frame failures are presented honestly in the media status UI", () => {
  assert.equal(exactFrameFailureMessage(404), "Exact frame is missing (404).");
});
