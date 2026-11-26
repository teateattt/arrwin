import assert from "node:assert/strict";
import test from "node:test";
import { validateAgentEnvelope } from "../validate-response.mjs";

test("accepts structured output", () => {
  const result = validateAgentEnvelope({
    status: "SUCCESS",
    structured_output: {
      reply: "Use a bounded retry policy.",
      skip: false
    }
  });

  assert.deepEqual(result, {
    reply: "Use a bounded retry policy.",
    skip: false
  });
});

test("accepts a JSON string response when structured_output is absent", () => {
  const result = validateAgentEnvelope({
    status: "SUCCESS",
    response: "{\"reply\":\"\",\"skip\":true}"
  });

  assert.deepEqual(result, {
    reply: "",
    skip: true
  });
});

test("rejects an empty publishable response", () => {
  assert.throws(
    () =>
      validateAgentEnvelope({
        status: "SUCCESS",
        structured_output: {
          reply: "   ",
          skip: false
        }
      }),
    /empty reply/
  );
});

test("rejects failed Agy envelopes", () => {
  assert.throws(
    () =>
      validateAgentEnvelope({
        status: "ERROR",
        error: "authentication required"
      }),
    /authentication required/
  );
});
