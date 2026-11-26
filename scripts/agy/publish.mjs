import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  AGY_MARKER_PREFIX,
  collectGitHubPages,
  requestGitHubJson
} from "./context.mjs";

const DEFAULT_API_BASE_URL = "https://api.github.com";

function parseRepository(fullName) {
  const [owner, name, ...rest] = String(fullName ?? "").split("/");

  if (
    !owner ||
    !name ||
    rest.length > 0 ||
    !/^[A-Za-z0-9_.-]+$/.test(owner) ||
    !/^[A-Za-z0-9_.-]+$/.test(name)
  ) {
    throw new Error("GITHUB_REPOSITORY must use the owner/repository format");
  }

  return { owner, name, fullName: `${owner}/${name}` };
}

function validateEventId(eventId) {
  if (
    typeof eventId !== "string" ||
    !eventId ||
    eventId.length > 200 ||
    !/^[A-Za-z0-9._-]+$/.test(eventId)
  ) {
    throw new Error("Context event id is invalid");
  }

  return eventId;
}

export function createEventMarker(eventId) {
  return `${AGY_MARKER_PREFIX}${validateEventId(eventId)} -->`;
}

export function createCommentBody(reply, eventId) {
  const marker = createEventMarker(eventId);

  return [
    reply.trim(),
    "",
    marker,
    "",
    "_Agy 自动生成。_"
  ].join("\n");
}

export function validatePublisherContext(context, repository) {
  if (!context || typeof context !== "object") {
    throw new Error("Context payload is missing");
  }

  const repo = parseRepository(repository);

  if (context.repository?.full_name !== repo.fullName) {
    throw new Error("Context repository does not match GITHUB_REPOSITORY");
  }

  const number = Number(context.target?.number);
  if (!Number.isSafeInteger(number) || number <= 0) {
    throw new Error("Context target number is invalid");
  }

  if (context.target?.reply_surface !== "conversation") {
    throw new Error("Unsupported reply surface for phase one");
  }

  if (!["issue", "pull_request"].includes(context.target?.kind)) {
    throw new Error("Unsupported target kind");
  }

  if (
    context.event?.kind !== "issues" &&
    context.event?.kind !== "issue_comment"
  ) {
    throw new Error("Unsupported context event kind");
  }

  return {
    owner: repo.owner,
    name: repo.name,
    number,
    eventId: validateEventId(context.event?.id)
  };
}

export async function publishResponse({
  context,
  response,
  repository,
  token,
  fetchImpl = globalThis.fetch,
  apiBaseUrl = DEFAULT_API_BASE_URL
}) {
  const target = validatePublisherContext(context, repository);

  if (!response || response.skip || !String(response.reply ?? "").trim()) {
    return {
      skipped: true,
      reason: "agent_skip"
    };
  }

  const body = createCommentBody(response.reply, target.eventId);
  const marker = createEventMarker(target.eventId);
  const commentsPath = `/repos/${target.owner}/${target.name}/issues/${target.number}/comments`;
  const comments = await collectGitHubPages(
    `${commentsPath}?per_page=100`,
    {
      token,
      fetchImpl,
      apiBaseUrl,
      maxPages: 10
    }
  );

  if (comments.items.some((comment) => comment.body?.includes(marker))) {
    return {
      skipped: true,
      reason: "duplicate"
    };
  }

  const created = await requestGitHubJson(commentsPath, {
    token,
    fetchImpl,
    apiBaseUrl,
    method: "POST",
    body: { body },
    attempts: 1
  });

  return {
    skipped: false,
    commentId: created.data?.id ?? null,
    htmlUrl: created.data?.html_url ?? null
  };
}

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

async function main() {
  const workDir =
    process.env.AGY_WORK_DIR ?? path.join(process.cwd(), ".agy-work");
  const skipPath = path.join(workDir, "skip.json");
  const contextPath = path.join(workDir, "context.json");
  const responsePath = path.join(workDir, "response.json");

  try {
    await fs.access(skipPath);
    console.log("Publisher skipped because the event was intentionally ignored");
    return;
  } catch {
    // Continue when the skip file does not exist.
  }

  const context = await readJson(contextPath);
  const response = await readJson(responsePath);
  const repository = process.env.GITHUB_REPOSITORY;
  const token = process.env.GITHUB_TOKEN;
  const result = await publishResponse({
    context,
    response,
    repository,
    token
  });

  if (result.skipped) {
    console.log(`Publisher skipped: ${result.reason}`);
    return;
  }

  console.log(
    `Published Agy comment: id=${result.commentId ?? "unknown"}, url=${result.htmlUrl ?? "unknown"}`
  );
}

const isMain =
  process.argv[1] &&
  pathToFileURL(path.resolve(process.argv[1])).href ===
    import.meta.url;

if (isMain) {
  main().catch((error) => {
    console.error(`Publishing failed: ${error.message}`);
    process.exitCode = 1;
  });
}
