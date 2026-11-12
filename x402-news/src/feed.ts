/**
 * The news feed sold behind the x402 paywall.
 *
 * Recorded mode replays a saved list of posts on a clock that starts when the
 * server starts (or on reset). Some posts are planted attacks. To the buyer a
 * planted post looks exactly like any other post: the `planted` and `attack`
 * fields never leave this process except through the admin view.
 *
 * Everything this feed returns is UNTRUSTED content. The agent runtime
 * registers each item in its Context Ledger before the LLM sees it.
 */

export type Source = "x" | "news";

/** What a buyer receives. */
export interface NewsItem {
  id: string;
  source: Source;
  author: string;
  text: string;
  url: string;
  timestamp: string;
}

/** One line of the recorded feed file. */
export interface RecordedEntry {
  id: string;
  source: Source;
  author: string;
  text: string;
  url: string;
  /** Seconds after replay start at which the post appears. */
  offsetSeconds: number;
  /** Topic tags. A query also matches on the text itself. */
  topics: string[];
  planted?: boolean;
  /** Attack category, for the admin view and the red-team scorecard. */
  attack?: string;
}

export interface InjectRequest {
  text: string;
  author?: string;
  source?: Source;
  url?: string;
  /** Omit to match every topic, so the post reaches whatever the agent asks for. */
  topics?: string[];
  attack?: string;
}

export interface AdminItem extends NewsItem {
  planted: boolean;
  attack: string | null;
  injected: boolean;
}

interface StoredItem extends NewsItem {
  topics: string[] | "*";
  planted: boolean;
  attack: string | null;
  injected: boolean;
  atMs: number;
  order: number;
}

export interface FeedOptions {
  now: () => number;
  /** Values for `{{NAME}}` placeholders in post text, e.g. ATTACKER_ADDRESS. */
  substitutions: Record<string, string>;
}

const PLACEHOLDER = /\{\{([A-Z0-9_]+)\}\}/g;
const SOURCES: readonly string[] = ["x", "news"];

export function parseRecordedFeed(json: string): RecordedEntry[] {
  const raw: unknown = JSON.parse(json);
  if (!Array.isArray(raw)) throw new Error("recorded feed must be a JSON array");
  const seen = new Set<string>();
  return raw.map((entry, index) => {
    const where = `recorded feed entry ${index}`;
    if (typeof entry !== "object" || entry === null) throw new Error(`${where}: not an object`);
    const e = entry as Record<string, unknown>;
    for (const field of ["id", "author", "text", "url"] as const) {
      if (typeof e[field] !== "string" || e[field] === "") {
        throw new Error(`${where}: ${field} must be a non-empty string`);
      }
    }
    if (!SOURCES.includes(e.source as string)) throw new Error(`${where}: source must be "x" or "news"`);
    if (typeof e.offsetSeconds !== "number" || !(e.offsetSeconds >= 0)) {
      throw new Error(`${where}: offsetSeconds must be a number >= 0`);
    }
    if (!Array.isArray(e.topics) || e.topics.some((t) => typeof t !== "string")) {
      throw new Error(`${where}: topics must be an array of strings`);
    }
    if (seen.has(e.id as string)) throw new Error(`${where}: duplicate id ${e.id}`);
    seen.add(e.id as string);
    return entry as RecordedEntry;
  });
}

export class Feed {
  private readonly entries: RecordedEntry[];
  private readonly options: FeedOptions;
  private startMs: number;
  private injected: StoredItem[] = [];
  private injectCount = 0;

  constructor(entries: RecordedEntry[], options: FeedOptions) {
    this.options = options;
    // Resolve placeholders up front so a missing value fails at startup.
    this.entries = entries.map((entry) => ({ ...entry, text: this.fill(entry.text) }));
    this.startMs = options.now();
  }

  /** Restart the replay clock and forget injected posts. */
  reset(): void {
    this.startMs = this.options.now();
    this.injected = [];
    this.injectCount = 0;
  }

  /** Newest first. An empty topic returns everything visible. */
  query(topic: string, limit = 10): NewsItem[] {
    const wanted = topic.trim().toLowerCase();
    return this.visible()
      .filter((item) => matches(item, wanted))
      .slice(0, Math.max(0, limit))
      .map(toPublic);
  }

  inject(request: InjectRequest): NewsItem {
    this.injectCount += 1;
    const id = `inj-${this.injectCount}`;
    const atMs = this.options.now();
    const item: StoredItem = {
      id,
      source: request.source ?? "x",
      author: request.author ?? "@unknown_sender",
      text: this.fill(request.text),
      url: request.url ?? `https://example.invalid/post/${id}`,
      timestamp: new Date(atMs).toISOString(),
      topics: request.topics ?? "*",
      planted: true,
      attack: request.attack ?? "injected",
      injected: true,
      atMs,
      order: 1_000_000 + this.injectCount,
    };
    this.injected.push(item);
    return toPublic(item);
  }

  adminView(): { startedAt: string; pending: number; items: AdminItem[] } {
    const visible = this.visible();
    return {
      startedAt: new Date(this.startMs).toISOString(),
      pending: this.entries.length + this.injected.length - visible.length,
      items: visible.map((item) => ({
        ...toPublic(item),
        planted: item.planted,
        attack: item.attack,
        injected: item.injected,
      })),
    };
  }

  private visible(): StoredItem[] {
    const elapsedSeconds = (this.options.now() - this.startMs) / 1000;
    const recorded = this.entries
      .filter((entry) => entry.offsetSeconds <= elapsedSeconds)
      .map((entry, index): StoredItem => {
        const atMs = this.startMs + entry.offsetSeconds * 1000;
        return {
          id: entry.id,
          source: entry.source,
          author: entry.author,
          text: entry.text,
          url: entry.url,
          timestamp: new Date(atMs).toISOString(),
          topics: entry.topics,
          planted: entry.planted ?? false,
          attack: entry.attack ?? null,
          injected: false,
          atMs,
          order: index,
        };
      });
    return [...recorded, ...this.injected].sort((a, b) => b.atMs - a.atMs || b.order - a.order);
  }

  private fill(text: string): string {
    const filled = text.replace(PLACEHOLDER, (_, name: string) => {
      const value = this.options.substitutions[name];
      if (!value) throw new Error(`feed text uses {{${name}}} but no value was provided for ${name}`);
      return value;
    });
    return filled.replace(ENCODED, (_, kind: string, inner: string) => ENCODERS[kind](inner));
  }
}

/**
 * `[[base64:...]]`, `[[hex:...]]` and `[[tags:...]]` segments are encoded after
 * placeholders are filled, so an attack post can hide an address that is only
 * known at runtime. `tags` uses invisible Unicode tag characters.
 */
const ENCODED = /\[\[(base64|hex|tags):([\s\S]*?)\]\]/g;
const ENCODERS: Record<string, (inner: string) => string> = {
  base64: (inner) => Buffer.from(inner, "utf8").toString("base64"),
  hex: (inner) => Buffer.from(inner, "utf8").toString("hex"),
  tags: (inner) =>
    Array.from(inner)
      .map((ch) => {
        const cp = ch.codePointAt(0)!;
        return cp >= 0x20 && cp <= 0x7e ? String.fromCodePoint(0xe0000 + cp) : ch;
      })
      .join(""),
};

function matches(item: StoredItem, wanted: string): boolean {
  if (wanted === "" || item.topics === "*") return true;
  return item.topics.some((t) => t.toLowerCase() === wanted) || item.text.toLowerCase().includes(wanted);
}

function toPublic(item: StoredItem): NewsItem {
  return {
    id: item.id,
    source: item.source,
    author: item.author,
    text: item.text,
    url: item.url,
    timestamp: item.timestamp,
  };
}
