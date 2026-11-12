/**
 * HTTP surface of the news service.
 *
 *   GET  /health         free; liveness and feed mode
 *   GET  /news?topic=    PAID (x402). Returns untrusted posts.
 *   POST /admin/inject   local + token only. Drops an attack post into the feed.
 *   POST /admin/reset    local + token only. Restarts the replay.
 *   GET  /admin/feed     local + token only. Shows which posts are planted.
 *
 * The admin routes change what a money-holding agent reads, so they are an
 * attack surface. They require a loopback connection with no proxy headers
 * AND a bearer token, and are disabled outright if no token is configured.
 */

import { timingSafeEqual } from "node:crypto";
import type { IncomingHttpHeaders } from "node:http";

import express, { type NextFunction, type Request, type RequestHandler, type Response } from "express";

import type { Feed, InjectRequest, Source } from "./feed.ts";

export type FeedMode = "recorded" | "live";

export interface AppOptions {
  feed: Feed;
  feedMode: FeedMode;
  /** x402 payment middleware guarding GET /news. */
  paywall: RequestHandler;
  /** Empty string disables the admin routes. */
  adminToken: string;
}

const LOOPBACK = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);
const PROXY_HEADERS = ["x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded", "cf-connecting-ip", "via"];
const MAX_TEXT = 4000;
const MAX_LIMIT = 25;

/** True only for a direct connection from this machine (no proxy or tunnel in between). */
export function isLocalRequest(remoteAddress: string | undefined, headers: IncomingHttpHeaders): boolean {
  if (!remoteAddress || !LOOPBACK.has(remoteAddress)) return false;
  return !PROXY_HEADERS.some((name) => headers[name] !== undefined);
}

export function tokenMatches(presented: string | undefined, expected: string): boolean {
  if (!presented || !expected) return false;
  const a = Buffer.from(presented);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

function parseInject(body: unknown): InjectRequest | string {
  if (typeof body !== "object" || body === null) return "body must be a JSON object";
  const b = body as Record<string, unknown>;
  if (typeof b.text !== "string" || b.text.trim() === "") return "text must be a non-empty string";
  if (b.text.length > MAX_TEXT) return `text must be at most ${MAX_TEXT} characters`;
  if (b.source !== undefined && b.source !== "x" && b.source !== "news") return 'source must be "x" or "news"';
  for (const field of ["author", "url", "attack"] as const) {
    if (b[field] !== undefined && typeof b[field] !== "string") return `${field} must be a string`;
  }
  if (b.topics !== undefined && !(Array.isArray(b.topics) && b.topics.every((t) => typeof t === "string"))) {
    return "topics must be an array of strings";
  }
  return {
    text: b.text,
    source: b.source as Source | undefined,
    author: b.author as string | undefined,
    url: b.url as string | undefined,
    attack: b.attack as string | undefined,
    topics: b.topics as string[] | undefined,
  };
}

export function createApp(options: AppOptions): express.Express {
  const { feed, feedMode, paywall, adminToken } = options;
  const app = express();
  app.disable("x-powered-by");

  app.get("/health", (_req, res) => {
    res.json({ ok: true, feedMode, label: feedMode === "recorded" ? "RECORDED FEED" : "LIVE FEED" });
  });

  app.get("/news", paywall, (req: Request, res: Response) => {
    const topic = typeof req.query.topic === "string" ? req.query.topic : "";
    const requested = Number.parseInt(String(req.query.limit ?? "10"), 10);
    const limit = Number.isFinite(requested) ? Math.min(Math.max(requested, 1), MAX_LIMIT) : 10;
    res.json({
      feedMode,
      label: feedMode === "recorded" ? "RECORDED FEED" : "LIVE FEED",
      topic,
      items: feed.query(topic, limit),
    });
  });

  const admin = express.Router();
  admin.use((req: Request, res: Response, next: NextFunction) => {
    if (!adminToken) {
      res.status(404).json({ error: "admin routes are disabled (NEWS_ADMIN_TOKEN is not set)" });
      return;
    }
    if (!isLocalRequest(req.socket.remoteAddress, req.headers)) {
      res.status(403).json({ error: "admin routes accept direct local connections only" });
      return;
    }
    const header = req.headers.authorization ?? "";
    const presented = header.startsWith("Bearer ") ? header.slice(7) : undefined;
    if (!tokenMatches(presented, adminToken)) {
      res.status(401).json({ error: "missing or invalid admin token" });
      return;
    }
    next();
  });
  admin.use(express.json({ limit: "32kb" }));

  admin.post("/inject", (req: Request, res: Response) => {
    const parsed = parseInject(req.body);
    if (typeof parsed === "string") {
      res.status(400).json({ error: parsed });
      return;
    }
    try {
      res.status(201).json({ item: feed.inject(parsed) });
    } catch (error) {
      res.status(400).json({ error: (error as Error).message });
    }
  });

  admin.post("/reset", (_req, res) => {
    feed.reset();
    res.json({ ok: true });
  });

  admin.get("/feed", (_req, res) => {
    res.json({ feedMode, ...feed.adminView() });
  });

  app.use("/admin", admin);

  app.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    const status = (error as { status?: number }).status === 400 ? 400 : 500;
    res.status(status).json({ error: status === 400 ? "malformed request body" : "internal error" });
  });

  return app;
}
