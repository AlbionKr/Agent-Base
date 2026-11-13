// The design rules in docs/design-notes.md, checked on every test run.
// Each rule names the habit it stops; a failure lists the exact selector and value.
import { describe, expect, it } from "vitest";
import stylesheet from "./styles.css?raw";

const css = stylesheet.replace(/\/\*[\s\S]*?\*\//g, "");
const sources = import.meta.glob(["./*.{ts,tsx}", "./space/*.{ts,tsx}", "!./*.test.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

type Decl = { selector: string; prop: string; value: string };

/** Every declaration with the selector of the innermost block it sits in. */
function declarations(text: string): Decl[] {
  const out: Decl[] = [];
  for (const m of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].trim();
    for (const part of m[2].split(";")) {
      const i = part.indexOf(":");
      if (i < 0) continue;
      out.push({ selector, prop: part.slice(0, i).trim(), value: part.slice(i + 1).trim() });
    }
  }
  return out;
}

const all = declarations(css);
// custom properties are where the scales are defined; everything else must use them
const used = all.filter((d) => !d.prop.startsWith("--"));
const outsideRoot = all.filter((d) => d.selector !== ":root");
const px = (value: string) => [...value.matchAll(/(?<![\w/.-])(-?\d*\.?\d+)px/g)].map((m) => Number(m[1]));
const show = (ds: Decl[]) => ds.map((d) => `${d.selector} { ${d.prop}: ${d.value} }`);

const TYPE_SCALE = [11, 12, 13, 14, 16, 20, 24, 32, 40];
const MONO_ALLOWED = ["code", ".logrow time", ".meta", ".address", ".posts li .hud", ".news .src"];

describe("design rules", () => {
  it("font sizes come from the type scale", () => {
    const bad = used.filter((d) => (d.prop === "font-size" || d.prop === "font") && px(d.value).some((n) => !TYPE_SCALE.includes(n)));
    expect(show(bad)).toEqual([]);
  });

  it("spacing sits on the 4px scale (1-2px allowed for hairlines)", () => {
    const spacing = /^(padding|margin|gap|row-gap|column-gap)(-(top|right|bottom|left))?$/;
    const bad = used.filter((d) => spacing.test(d.prop) && px(d.value).some((n) => Math.abs(n) > 2 && n % 4 !== 0));
    expect(show(bad)).toEqual([]);
  });

  it("corner radii come from the radius tokens, pills or circles", () => {
    const bad = used.filter((d) => d.prop.startsWith("border") && d.prop.endsWith("radius") && px(d.value).some((n) => n !== 2 && n !== 999 && n !== 0));
    expect(show(bad)).toEqual([]);
  });

  it("labels are never uppercase or tracked out", () => {
    const bad = used.filter((d) => (d.prop === "text-transform" && d.value === "uppercase") || (d.prop === "letter-spacing" && !d.value.startsWith("-") && d.value !== "0"));
    expect(show(bad)).toEqual([]);
  });

  it("only three weights: regular, medium, semibold", () => {
    const bad = used.filter((d) => {
      if (d.prop === "font-weight") return !["400", "500", "600"].includes(d.value);
      if (d.prop === "font") return /^\d{3}\b/.test(d.value) && !/^(400|500|600)\b/.test(d.value);
      return false;
    });
    expect(show(bad)).toEqual([]);
  });

  it("monospace is only for machine data: addresses, hashes, event ids, times", () => {
    const bad = used.filter((d) => d.value.includes("var(--mono)") && !MONO_ALLOWED.includes(d.selector));
    expect(show(bad)).toEqual([]);
  });

  it("colours come from the tokens: no hex or tinted rgba outside :root", () => {
    const neutral = new Set(["255,255,255", "0,0,0", "5,6,10"]);
    const bad = outsideRoot.filter(
      (d) =>
        /#[0-9a-f]{3,8}\b/i.test(d.value) ||
        /\b(rgb|hsl)a?\(/.test(d.value.replace(/rgba\(\s*(\d+),\s*(\d+),\s*(\d+),[^)]*\)/g, (m, r, g, b) => (neutral.has(`${r},${g},${b}`) ? "" : m))),
    );
    expect(show(bad)).toEqual([]);
  });

  it("no gradient text and no linear-gradient washes", () => {
    const bad = used.filter((d) => /background-clip/.test(d.prop) || d.value.includes("linear-gradient"));
    expect(show(bad)).toEqual([]);
  });

  it("interface transitions are short: 300ms or less", () => {
    const bad = used.filter((d) => d.prop === "transition" && [...d.value.matchAll(/(\d*\.?\d+)s\b/g)].some((m) => Number(m[1]) > 0.3));
    expect(show(bad)).toEqual([]);
  });

  it("type faces are set only through the two font tokens", () => {
    const bad = used.filter((d) => d.prop === "font-family" && !/^var\(--(ui|mono)\)$/.test(d.value));
    expect(show(bad)).toEqual([]);
  });
});

describe("copy rules", () => {
  it("reads the interface sources", () => {
    expect(Object.keys(sources)).toEqual(expect.arrayContaining(["./App.tsx", "./describe.ts", "./space/Space.tsx"]));
  });

  it("no placeholder text, arrows on buttons or middle-dot meta strings", () => {
    const bad: string[] = [];
    for (const [f, text] of Object.entries(sources)) {
      for (const banned of ["→", " · ", "Lorem", "lorem ipsum", "John Doe", "Jane Doe", "TODO"]) {
        if (text.includes(banned)) bad.push(`${f}: ${banned}`);
      }
    }
    expect(bad).toEqual([]);
  });
});
