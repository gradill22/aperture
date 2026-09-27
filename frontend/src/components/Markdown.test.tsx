import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown, hasMarkdown } from "./Markdown";

const html = (text: string) => renderToStaticMarkup(<Markdown text={text} />);

describe("Markdown", () => {
  it("renders emphasis, code, lists and headings", () => {
    const out = html("## Hits\n\n**3** aircraft, `a00929` first:\n\n- N101HQ\n- N7GH\n\n1. one\n2. two");
    expect(out).toContain("<h2>Hits</h2>");
    expect(out).toContain("<strong>3</strong>");
    expect(out).toContain("<code>a00929</code>");
    expect(out).toContain("<ul>\n<li>N101HQ</li>");
    expect(out).toContain("<ol>");
  });

  it("renders GFM tables, strikethrough and autolinks", () => {
    const out = html("| icao24 | min km |\n|---|---:|\n| a00929 | 1.2 |\n\n~~old~~ see www.example.org");
    expect(out).toContain("<table>");
    expect(out).toContain("<th>icao24</th>");
    expect(out).toContain('<td style="text-align:right">1.2</td>');
    expect(out).toContain("<del>old</del>");
    expect(out).toMatch(/<a href="http:\/\/www\.example\.org"[^>]*>www\.example\.org<\/a>/);
  });

  it("opens links in a new tab without an opener or referrer", () => {
    const out = html("[docs](/provenance.json)");
    expect(out).toBe(
      '<div class="md"><p><a href="/provenance.json" target="_blank" rel="noopener noreferrer">docs</a></p></div>',
    );
  });

  it("drops script URLs", () => {
    const out = html("[click](javascript:alert(1))");
    expect(out).not.toContain("javascript:");
  });

  it("never renders raw HTML", () => {
    const out = html('hi <script>alert(1)</script> <img src="/x.png" onerror="alert(1)"> <b>bold</b>');
    expect(out).not.toMatch(/<(script|img|b)[\s>]/);
    expect(out).not.toContain("onerror");
  });

  it("shows images as alt text, so nothing is fetched", () => {
    const out = html("![radar plot](/tiles/x.png) and ![](/y.png)");
    expect(out).not.toContain("<img");
    expect(out).toContain('<span class="img-alt">[radar plot]</span>');
    expect(out).toContain('<span class="img-alt">[image]</span>');
  });

  it("keeps plain text as a paragraph", () => {
    expect(html("N101HQ (a00929) flew 9 legs.")).toBe('<div class="md"><p>N101HQ (a00929) flew 9 legs.</p></div>');
  });
});

describe("hasMarkdown", () => {
  it.each([
    "**bold**",
    "some _emphasis_",
    "`code`",
    "# title",
    "> quote",
    "- item",
    "  * item",
    "1. first",
    "[link](/x)",
    "| a | b |",
    "---",
    "~~gone~~",
    "line one\n- item",
  ])("detects %j", (text) => expect(hasMarkdown(text)).toBe(true));

  it.each([
    "",
    "Show me the track of N101HQ.",
    "Which aircraft passed within 2 km of Reagan National between 14:00 and 15:00 UTC?",
    "2 * 3 = 6",
    "call-sign N7GH-2",
    "#hashtag",
  ])("ignores %j", (text) => expect(hasMarkdown(text)).toBe(false));
});
