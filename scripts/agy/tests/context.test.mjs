import assert from "node:assert/strict";
import test from "node:test";
import {
  buildContext,
  buildEventIdentity,
  shouldSkipEvent
} from "../context.mjs";

function jsonResponse(data, link = null) {
  return {
    ok: true,
    status: 200,
    headers: {
      get(name) {
        return name.toLowerCase() === "link" ? link : null;
      }
    },
    json: async () => data,
    text: async () => ""
  };
}

function errorResponse(status, text) {
  return {
    ok: false,
    status,
    headers: {
      get() {
        return null;
      }
    },
    json: async () => null,
    text: async () => text
  };
}

function issueCommentEvent(overrides = {}) {
  return {
    action: "created",
    repository: {
      default_branch: "main"
    },
    issue: {
      id: 101,
      number: 42,
      user: {
        login: "reporter",
        type: "User"
      }
    },
    comment: {
      id: 900,
      body: "Could you explain how the retry should work?",
      user: {
        login: "alice",
        type: "User"
      },
      author_association: "CONTRIBUTOR",
      created_at: "2026-11-26T10:00:00Z"
    },
    sender: {
      login: "alice",
      type: "User"
    },
    ...overrides
  };
}

test("builds issue comment context with Agent history and no duplicate current comment", async () => {
  const event = issueCommentEvent();
  const requestedPaths = [];
  const fetchImpl = async (url) => {
    const pathname = new URL(url).pathname;
    requestedPaths.push(pathname);

    if (pathname.endsWith("/issues/42")) {
      return jsonResponse({
        id: 101,
        number: 42,
        title: "Retry policy",
        body: "Please define how retries should work.",
        state: "open",
        labels: [{ name: "design" }],
        user: {
          login: "reporter",
          type: "User"
        },
        author_association: "MEMBER",
        pull_request: {}
      });
    }

    if (pathname.endsWith("/issues/42/comments")) {
      return jsonResponse([
        {
          id: 700,
          body: "We should avoid duplicate writes.",
          user: {
            login: "maintainer",
            type: "User"
          },
          author_association: "MEMBER",
          created_at: "2026-11-26T09:00:00Z"
        },
        {
          id: 800,
          body: "A previous Agy answer.\n<!-- agy:event:comment-created-700 -->",
          user: {
            login: "github-actions[bot]",
            type: "Bot"
          },
          author_association: "NONE",
          created_at: "2026-11-26T09:30:00Z"
        },
        {
          id: 850,
          body: "Unrelated bot output",
          user: {
            login: "other[bot]",
            type: "Bot"
          },
          author_association: "NONE"
        },
        {
          id: 900,
          body: event.comment.body,
          user: event.comment.user,
          author_association: "CONTRIBUTOR"
        }
      ]);
    }

    throw new Error(`Unexpected request: ${url}`);
  };

  const context = await buildContext({
    event,
    eventName: "issue_comment",
    repository: "owner/repo",
    token: "test-token",
    fetchImpl,
    limits: {
      maxComments: 20,
      maxCommentChars: 1000,
      maxCommentPages: 1
    }
  });

  assert.equal(context.event.id, "comment-created-900");
  assert.equal(context.target.kind, "pull_request");
  assert.equal(context.target.number, 42);
  assert.equal(context.thread.current_input.body, event.comment.body);
  assert.equal(context.thread.comments.length, 2);
  assert.equal(context.thread.comments[1].is_agy_response, true);
  assert.equal(context.thread.truncated, false);
  assert.deepEqual(requestedPaths, [
    "/repos/owner/repo/issues/42",
    "/repos/owner/repo/issues/42/comments"
  ]);
});

test("skips bot senders without calling GitHub", async () => {
  const event = issueCommentEvent({
    sender: {
      login: "github-actions[bot]",
      type: "Bot"
    }
  });
  let requestCount = 0;

  const context = await buildContext({
    event,
    eventName: "issue_comment",
    repository: "owner/repo",
    token: "test-token",
    fetchImpl: async () => {
      requestCount += 1;
      throw new Error("GitHub should not be called");
    }
  });

  assert.deepEqual(context, {
    skip: true,
    reason: "bot_sender"
  });
  assert.equal(requestCount, 0);
});

test("detects Event identity and Agy marker", () => {
  const issueEvent = {
    action: "opened",
    issue: { id: 123 },
    sender: { login: "reporter", type: "User" }
  };
  const commentEvent = issueCommentEvent({
    comment: {
      ...issueCommentEvent().comment,
      body: "No response needed\n<!-- agy:event:comment-created-900 -->"
    }
  });

  assert.equal(buildEventIdentity(issueEvent, "issues"), "issue-opened-123");
  assert.deepEqual(shouldSkipEvent(commentEvent, "issue_comment"), {
    skip: true,
    reason: "agy_marker"
  });
});

test("does not retry non-retryable GitHub API failures", async () => {
  let requestCount = 0;

  await assert.rejects(
    buildContext({
      event: issueCommentEvent(),
      eventName: "issue_comment",
      repository: "owner/repo",
      token: "test-token",
      fetchImpl: async () => {
        requestCount += 1;
        return errorResponse(401, "Unauthorized");
      }
    }),
    /GitHub API 401/
  );

  assert.equal(requestCount, 1);
});
