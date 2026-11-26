import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const AGY_MARKER_PREFIX = "<!-- agy:event:";
export const DEFAULT_API_BASE_URL = "https://api.github.com";

const ALLOWED_EVENTS = new Set(["issues", "issue_comment"]);
const DEFAULT_LIMITS = Object.freeze({
  maxComments: 20,
  maxCommentChars: 4000,
  maxSubjectBodyChars: 16000,
  maxCommentPages: 3
});

function intFromEnv(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }

  return Math.min(maximum, Math.max(minimum, parsed));
}

function truncateText(value, maxChars) {
  const source = typeof value === "string" ? value : "";
  const text = source.slice(0, maxChars);

  return {
    text,
    truncated: source.length > text.length
  };
}

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

function parsePositiveInteger(value, label) {
  const parsed = Number.parseInt(String(value ?? ""), 10);

  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`Invalid ${label}`);
  }

  return parsed;
}

function actorLogin(actor) {
  return actor?.login ?? "unknown";
}

export function isBotActor(actor) {
  const login = actorLogin(actor).toLowerCase();

  return actor?.type === "Bot" || login.endsWith("[bot]");
}

function hasAgyMarker(body) {
  return typeof body === "string" && body.includes(AGY_MARKER_PREFIX);
}

export function shouldSkipEvent(event, eventName) {
  if (!ALLOWED_EVENTS.has(eventName)) {
    throw new Error(`Unsupported event: ${eventName}`);
  }

  if (eventName === "issues" && event.action !== "opened") {
    throw new Error(`Unsupported issues action: ${event.action}`);
  }

  if (eventName === "issue_comment" && event.action !== "created") {
    throw new Error(`Unsupported issue_comment action: ${event.action}`);
  }

  const sender = event.sender ?? event.comment?.user ?? event.issue?.user;

  if (isBotActor(sender)) {
    return {
      skip: true,
      reason: "bot_sender"
    };
  }

  if (eventName === "issue_comment" && hasAgyMarker(event.comment?.body)) {
    return {
      skip: true,
      reason: "agy_marker"
    };
  }

  return {
    skip: false,
    reason: null
  };
}

export function buildEventIdentity(event, eventName) {
  if (eventName === "issues") {
    return `issue-opened-${parsePositiveInteger(event.issue?.id, "issue id")}`;
  }

  return `comment-created-${parsePositiveInteger(event.comment?.id, "comment id")}`;
}

function parseNextLink(linkHeader) {
  if (!linkHeader) {
    return null;
  }

  const match = linkHeader.match(/<([^>]+)>\s*;\s*rel="next"/i);
  return match?.[1] ?? null;
}

function sleep(milliseconds) {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

function resolveApiUrl(resource, apiBaseUrl) {
  if (/^https:\/\//i.test(resource)) {
    return resource;
  }

  return new URL(resource, `${apiBaseUrl.replace(/\/$/, "")}/`).toString();
}

export async function requestGitHubJson(
  resource,
  {
    token,
    fetchImpl = globalThis.fetch,
    apiBaseUrl = DEFAULT_API_BASE_URL,
    method = "GET",
    body,
    attempts = 3
  } = {}
) {
  if (!token) {
    throw new Error("GitHub token is required");
  }

  if (typeof fetchImpl !== "function") {
    throw new Error("A fetch implementation is required");
  }

  const url = resolveApiUrl(resource, apiBaseUrl);
  const requestAttempts = Math.max(1, attempts);
  let lastError;

  for (let attempt = 1; attempt <= requestAttempts; attempt += 1) {
    try {
      const response = await fetchImpl(url, {
        method,
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28",
          ...(body ? { "Content-Type": "application/json" } : {})
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
        signal: AbortSignal.timeout(20000)
      });

      if (response.ok) {
        return {
          data: response.status === 204 ? null : await response.json(),
          nextUrl: parseNextLink(response.headers.get("link"))
        };
      }

      const responseText = (await response.text()).slice(0, 500);
      lastError = new Error(
        `GitHub API ${response.status} for ${new URL(url).pathname}: ${responseText}`
      );

      const retryable =
        response.status === 429 ||
        response.status === 408 ||
        response.status >= 500;
      lastError.retryable = retryable;

      if (!retryable || attempt === requestAttempts) {
        throw lastError;
      }
    } catch (error) {
      lastError = error;

      if (error?.retryable === false || attempt === requestAttempts) {
        throw error;
      }
    }

    await sleep(500 * 2 ** (attempt - 1));
  }

  throw lastError ?? new Error("GitHub request failed");
}

export async function collectGitHubPages(
  resource,
  {
    maxPages = 3,
    ...requestOptions
  } = {}
) {
  const items = [];
  let nextUrl = resource;
  let pagesScanned = 0;

  while (nextUrl && pagesScanned < maxPages) {
    const result = await requestGitHubJson(nextUrl, requestOptions);

    if (!Array.isArray(result.data)) {
      throw new Error(`Expected an array response for ${resource}`);
    }

    items.push(...result.data);
    nextUrl = result.nextUrl;
    pagesScanned += 1;
  }

  return {
    items,
    pagesScanned,
    hasMore: Boolean(nextUrl)
  };
}

function normalizeHistoryComment(comment, maxCommentChars) {
  const body = truncateText(comment.body, maxCommentChars);

  return {
    id: comment.id,
    author: actorLogin(comment.user),
    author_type: comment.user?.type ?? "Unknown",
    author_association: comment.author_association ?? "NONE",
    body: body.text,
    body_truncated: body.truncated,
    created_at: comment.created_at ?? null,
    is_agy_response: hasAgyMarker(comment.body)
  };
}

function currentInputFor(event, eventName, maxCommentChars) {
  if (eventName === "issues") {
    return {
      type: "issue_lifecycle",
      action: "opened",
      changes: event.changes ?? null
    };
  }

  const body = truncateText(event.comment?.body, maxCommentChars);

  return {
    type: "issue_or_pr_comment",
    id: parsePositiveInteger(event.comment?.id, "comment id"),
    body: body.text,
    body_truncated: body.truncated,
    author: actorLogin(event.comment?.user),
    author_association: event.comment?.author_association ?? "NONE",
    created_at: event.comment?.created_at ?? null
  };
}

export async function buildContext({
  event,
  eventName,
  repository,
  token,
  fetchImpl = globalThis.fetch,
  apiBaseUrl = DEFAULT_API_BASE_URL,
  limits = {}
}) {
  const skipDecision = shouldSkipEvent(event, eventName);

  if (skipDecision.skip) {
    return skipDecision;
  }

  const repo = parseRepository(repository);
  const number = parsePositiveInteger(event.issue?.number, "issue number");
  const maxComments = limits.maxComments ?? DEFAULT_LIMITS.maxComments;
  const maxCommentChars =
    limits.maxCommentChars ?? DEFAULT_LIMITS.maxCommentChars;
  const maxSubjectBodyChars =
    limits.maxSubjectBodyChars ?? DEFAULT_LIMITS.maxSubjectBodyChars;
  const maxCommentPages =
    limits.maxCommentPages ?? DEFAULT_LIMITS.maxCommentPages;

  const issueResult = await requestGitHubJson(
    `/repos/${repo.owner}/${repo.name}/issues/${number}`,
    {
      token,
      fetchImpl,
      apiBaseUrl
    }
  );

  const issueData = issueResult.data;
  if (!issueData || typeof issueData !== "object") {
    throw new Error("GitHub returned an invalid Issue/PR payload");
  }

  const commentsResult = await collectGitHubPages(
    `/repos/${repo.owner}/${repo.name}/issues/${number}/comments?per_page=100`,
    {
      token,
      fetchImpl,
      apiBaseUrl,
      maxPages: maxCommentPages
    }
  );

  const currentCommentId = event.comment?.id;
  const history = commentsResult.items
    .filter((comment) => comment.id !== currentCommentId)
    .filter(
      (comment) =>
        !isBotActor(comment.user) || hasAgyMarker(comment.body)
    )
    .map((comment) => normalizeHistoryComment(comment, maxCommentChars));

  const selectedHistory = history.slice(-maxComments);
  const threadTruncated =
    commentsResult.hasMore ||
    history.length > selectedHistory.length ||
    selectedHistory.some((comment) => comment.body_truncated);
  const subjectBody = truncateText(issueData.body, maxSubjectBodyChars);
  const isPullRequest = Boolean(issueData.pull_request);
  const actor = event.sender ?? event.comment?.user ?? issueData.user;
  const authorAssociation =
    event.comment?.author_association ??
    issueData.author_association ??
    "NONE";

  return {
    schema_version: 1,
    repository: {
      owner: repo.owner,
      name: repo.name,
      full_name: repo.fullName,
      default_branch: event.repository?.default_branch ?? null
    },
    event: {
      id: buildEventIdentity(event, eventName),
      kind: eventName,
      action: event.action,
      actor: {
        login: actorLogin(actor),
        type: actor?.type ?? "Unknown",
        author_association: authorAssociation
      }
    },
    target: {
      kind: isPullRequest ? "pull_request" : "issue",
      number,
      reply_surface: "conversation"
    },
    subject: {
      id: issueData.id ?? null,
      title: issueData.title ?? "",
      body: subjectBody.text,
      body_truncated: subjectBody.truncated,
      state: issueData.state ?? "unknown",
      labels: Array.isArray(issueData.labels)
        ? issueData.labels.map((label) =>
            typeof label === "string" ? label : label?.name
          ).filter(Boolean)
        : [],
      author: actorLogin(issueData.user),
      author_association: issueData.author_association ?? "NONE",
      created_at: issueData.created_at ?? null,
      updated_at: issueData.updated_at ?? null,
      url: issueData.html_url ?? null
    },
    thread: {
      current_input: currentInputFor(event, eventName, maxCommentChars),
      comments: selectedHistory,
      total_comments_observed: history.length,
      truncated: threadTruncated
    },
    limits: {
      max_comments: maxComments,
      max_comment_characters: maxCommentChars,
      max_subject_body_characters: maxSubjectBodyChars
    }
  };
}

function limitsFromEnvironment() {
  return {
    maxComments: intFromEnv(
      process.env.AGY_MAX_COMMENTS,
      DEFAULT_LIMITS.maxComments,
      1,
      50
    ),
    maxCommentChars: intFromEnv(
      process.env.AGY_MAX_COMMENT_CHARS,
      DEFAULT_LIMITS.maxCommentChars,
      500,
      12000
    ),
    maxCommentPages: intFromEnv(
      process.env.AGY_MAX_COMMENT_PAGES,
      DEFAULT_LIMITS.maxCommentPages,
      1,
      10
    )
  };
}

async function writeJson(filePath, value) {
  await fs.writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

async function removeIfPresent(filePath) {
  await fs.rm(filePath, { force: true });
}

async function main() {
  const eventPath = process.env.GITHUB_EVENT_PATH;
  const eventName = process.env.GITHUB_EVENT_NAME;
  const repository = process.env.GITHUB_REPOSITORY;
  const token = process.env.GITHUB_TOKEN;
  const workDir =
    process.env.AGY_WORK_DIR ?? path.join(process.cwd(), ".agy-work");
  const contextPath = path.join(workDir, "context.json");
  const skipPath = path.join(workDir, "skip.json");

  if (!eventPath || !eventName || !repository) {
    throw new Error(
      "GITHUB_EVENT_PATH, GITHUB_EVENT_NAME and GITHUB_REPOSITORY are required"
    );
  }

  const event = JSON.parse(await fs.readFile(eventPath, "utf8"));
  await fs.mkdir(workDir, { recursive: true });
  await Promise.all([removeIfPresent(contextPath), removeIfPresent(skipPath)]);

  const context = await buildContext({
    event,
    eventName,
    repository,
    token,
    limits: limitsFromEnvironment()
  });

  if (context.skip) {
    await writeJson(skipPath, context);
    console.log(`Context skipped: ${context.reason}`);
    return;
  }

  await writeJson(contextPath, context);
  console.log(
    `Context prepared: ${context.event.kind}/${context.event.action}, target=${context.target.kind}#${context.target.number}, comments=${context.thread.comments.length}, truncated=${context.thread.truncated}`
  );
}

const isMain =
  process.argv[1] &&
  pathToFileURL(path.resolve(process.argv[1])).href ===
    pathToFileURL(fileURLToPath(import.meta.url)).href;

if (isMain) {
  main().catch((error) => {
    console.error(`Context preparation failed: ${error.message}`);
    process.exitCode = 1;
  });
}
