import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const DEFAULT_MAX_REPLY_CHARS = 12000;
const FORBIDDEN_OUTPUT_PATTERNS = [
  /<script\b/i,
  /<iframe\b/i,
  /javascript:/i
];

function maxReplyCharsFromEnvironment() {
  const parsed = Number.parseInt(process.env.AGY_MAX_REPLY_CHARS ?? "", 10);

  if (!Number.isFinite(parsed)) {
    return DEFAULT_MAX_REPLY_CHARS;
  }

  return Math.min(12000, Math.max(500, parsed));
}

function parseStructuredResponse(envelope) {
  if (envelope?.structured_output !== undefined) {
    return envelope.structured_output;
  }

  if (typeof envelope?.response !== "string") {
    throw new Error("Agy response is missing structured_output and response");
  }

  try {
    return JSON.parse(envelope.response);
  } catch {
    throw new Error("Agy response is not valid JSON");
  }
}

export function validateAgentEnvelope(
  envelope,
  { maxReplyChars = DEFAULT_MAX_REPLY_CHARS } = {}
) {
  if (!envelope || typeof envelope !== "object") {
    throw new Error("Agy returned no JSON envelope");
  }

  if (envelope.status !== "SUCCESS") {
    const detail =
      typeof envelope.error === "string" && envelope.error.trim()
        ? `: ${envelope.error.trim()}`
        : "";
    throw new Error(`Agy execution failed${detail}`);
  }

  const response = parseStructuredResponse(envelope);

  if (!response || typeof response !== "object" || Array.isArray(response)) {
    throw new Error("Agy structured output must be an object");
  }

  if (typeof response.skip !== "boolean") {
    throw new Error("Agy response field skip must be a boolean");
  }

  if (typeof response.reply !== "string") {
    throw new Error("Agy response field reply must be a string");
  }

  const reply = response.reply.trim();

  if (!response.skip && !reply) {
    throw new Error("Agy returned an empty reply without skip=true");
  }

  if (reply.length > maxReplyChars) {
    throw new Error(
      `Agy reply exceeds the ${maxReplyChars} character limit`
    );
  }

  if (FORBIDDEN_OUTPUT_PATTERNS.some((pattern) => pattern.test(reply))) {
    throw new Error("Agy reply contains forbidden active HTML");
  }

  return {
    reply,
    skip: response.skip
  };
}

export async function validateResponseFile(
  rawResponsePath,
  outputPath,
  options = {}
) {
  const raw = JSON.parse(await fs.readFile(rawResponsePath, "utf8"));
  const response = validateAgentEnvelope(raw, options);

  await fs.writeFile(
    outputPath,
    `${JSON.stringify(response, null, 2)}\n`,
    "utf8"
  );

  return {
    ...response,
    usage: raw.usage ?? null,
    duration_seconds: raw.duration_seconds ?? null
  };
}

async function main() {
  const rawResponsePath = process.argv[2];
  const outputPath = process.argv[3];

  if (!rawResponsePath || !outputPath) {
    throw new Error("Usage: validate-response.mjs <raw.json> <response.json>");
  }

  const result = await validateResponseFile(rawResponsePath, outputPath, {
    maxReplyChars: maxReplyCharsFromEnvironment()
  });

  console.log(
    `Agy response validated: skip=${result.skip}, reply_chars=${result.reply.length}, duration_seconds=${result.duration_seconds ?? "unknown"}, total_tokens=${result.usage?.total_tokens ?? "unknown"}`
  );
}

const isMain =
  process.argv[1] &&
  pathToFileURL(path.resolve(process.argv[1])).href ===
    import.meta.url;

if (isMain) {
  main().catch((error) => {
    console.error(`Response validation failed: ${error.message}`);
    process.exitCode = 1;
  });
}
