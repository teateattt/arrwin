import assert from "node:assert/strict";
import test from "node:test";
import {
  createCommentBody,
  publishResponse,
  validatePublisherContext
} from "../publish.mjs";

function jsonResponse(data, status = 200, link = null) {
  return {
    ok: true,
    status,
    headers: {
      get(name) {
        return name.toLowerCase() === "link" ? link : null;
      }
    },
    json: async () => data,
    text: async () => ""
  };
}

const context = {
  repository: {
    full_name: "owner/repo"
  },
  event: {
    id: "comment-created-900",
    kind: "issue_comment"
  },
  target: {
    kind: "pull_request",
    number: 42,
    reply_surface: "conversation"
  }
};

test("publishes a Comment with a deduplication marker and footer", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({
      url,
      method: options.method ?? "GET",
      body: options.body
    });

    if (options.method === "POST") {
      return jsonResponse(
        {
          id: 1001,
          html_url: "https://github.com/owner/repo/pull/42#issuecomment-1001"
        },
        201
      );
    }

    return jsonResponse([]);
  };

  const result = await publishResponse({
    context,
    response: {
      reply: "A bounded retry policy avoids duplicate writes.",
      skip: false
    },
    repository: "owner/repo",
    token: "test-token",
    fetchImpl
  });

  assert.equal(result.skipped, false);
  assert.equal(result.commentId, 1001);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].method, "POST");
  const postedBody = JSON.parse(calls[1].body).body;
  assert.match(postedBody, /A bounded retry policy/);
  assert.match(postedBody, /<!-- agy:event:comment-created-900 -->/);
  assert.match(postedBody, /Agy 自动生成/);
});

test("does not publish when the event marker already exists", async () => {
  let postCount = 0;
  const marker = "<!-- agy:event:comment-created-900 -->";
  const fetchImpl = async (_url, options = {}) => {
    if (options.method === "POST") {
      postCount += 1;
      throw new Error("Post should not be called");
    }

    return jsonResponse([
      {
        id: 1000,
        body: `Already answered\n\n${marker}`
      }
    ]);
  };

  const result = await publishResponse({
    context,
    response: {
      reply: "A duplicate answer.",
      skip: false
    },
    repository: "owner/repo",
    token: "test-token",
    fetchImpl
  });

  assert.deepEqual(result, {
    skipped: true,
    reason: "duplicate"
  });
  assert.equal(postCount, 0);
});

test("rejects a repository mismatch before publishing", () => {
  assert.throws(
    () => validatePublisherContext(context, "other/repo"),
    /does not match/
  );
});

test("creates Markdown bodies without relying on fixed line positions", () => {
  const body = createCommentBody(
    "First line\nSecond line",
    "comment-created-900"
  );

  assert.match(body, /First line\nSecond line/);
  assert.match(body, /<!-- agy:event:comment-created-900 -->/);
});
