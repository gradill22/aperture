import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Chat text is untrusted (model output, user input): raw HTML is dropped, and images are shown as
// their alt text so a message can never make the page fetch anything, on- or off-origin.
const COMPONENTS: Components = {
  a: ({ href, title, children }) => (
    <a href={href} title={title} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: ({ alt }) => <span className="img-alt">[{alt || "image"}]</span>,
};

const PLUGINS = [remarkGfm];

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={PLUGINS} components={COMPONENTS} skipHtml>
        {text}
      </ReactMarkdown>
    </div>
  );
}

// Emphasis, code, headings, quotes, lists, links, tables, rules: worth a composer preview.
const SYNTAX = /[*_~`]\S|^#{1,6}\s|^>\s?|^\s*(?:[-+*]|\d+[.)])\s|\[[^\]]*\]\(|^\s*\||^(?:-{3,}|\*{3,})\s*$/m;

export function hasMarkdown(text: string): boolean {
  return SYNTAX.test(text);
}
